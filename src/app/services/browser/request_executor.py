import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from app.config import CONFIG
from app.logger import logger
from app.schemas.request import OpenAIChatRequest
from app.services.browser.errors import (
    BrowserDisconnectedError,
    BrowserGenerationMismatchError,
    BrowserShuttingDownError,
    ConversationBusyError,
    LeaseInvalidatedError,
    QueueOverflowError,
    SessionNotAliveError,
    TransientSessionError,
)
from app.services.browser.tab import ManagedPage


@dataclass(frozen=True)
class PlaywrightAdapterConfig:
    """Consolidated configuration for browser-native request execution."""
    navigation_timeout: int
    ui_wait_timeout: int
    chunk_timeout: int
    total_request_timeout: int

    @classmethod
    def load(cls) -> "PlaywrightAdapterConfig":
        return cls(
            navigation_timeout=CONFIG["Playwright"].getint("navigation_timeout", 30000),
            ui_wait_timeout=CONFIG["Playwright"].getint("ui_wait_timeout", 15000),
            chunk_timeout=CONFIG["Playwright"].getint("chunk_timeout", 90),
            total_request_timeout=CONFIG["Playwright"].getint("total_request_timeout", 120),
        )


@dataclass
class PlaywrightRequestState:
    """Shared state for a single browser-native request lifecycle."""
    request_id: str
    start_time: float
    permit_acquired: bool = False
    cleanup_started: bool = False
    page_closed: bool = False
    page_poisoned: bool = False
    dropped_chunks: int = 0
    max_queue_depth: int = 0
    conversation_id: Optional[str] = None
    reused_conversation: bool = False
    active_tab: Optional[Any] = None
    js_ready: asyncio.Event = field(default_factory=asyncio.Event)
    submission_confirmed: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    has_sent_text: bool = False
    on_close_handler: Any = None
    on_crash_handler: Any = None
    queue_overflow: bool = False


@dataclass(frozen=True)
class BrowserRequestExecutorHooks:
    provider_name: str
    session_name: str
    callback_name: str
    bridge_callbacks_attr: str
    default_model: str
    create_browser_adapter: Callable[[], Any]
    get_browser_engine: Callable[[], Awaitable[Any]]
    sleep: Callable[[float], Awaitable[None]]
    timeout: Callable[[float], Any]
    navigate: Callable[[Page, PlaywrightRequestState, OpenAIChatRequest, PlaywrightAdapterConfig], Awaitable[None]]
    wait_for_ready_ui: Callable[[Page, PlaywrightRequestState, PlaywrightAdapterConfig], Awaitable[None]]
    start_observer: Callable[[Page, str, str], Awaitable[Any]]
    stop_observer: Callable[[Page, str], Awaitable[None]]
    stop_generation: Callable[[Page], Awaitable[None]]
    extract_conversation_id: Callable[[str], Optional[str]]
    convert_to_openai_format: Callable[[str, str, bool], dict]
    orchestrate_model_selection: Callable[[Any, Page, str, PlaywrightRequestState], Awaitable[None]]


class BrowserRequestExecutor:
    """
    Shared request-scoped browser execution lifecycle.

    Provider-specific DOM behavior remains delegated through hooks.
    """

    def __init__(self, config: PlaywrightAdapterConfig, hooks: BrowserRequestExecutorHooks):
        self.config = config
        self.hooks = hooks

    async def execute(self, request: OpenAIChatRequest) -> Any:
        request_id = getattr(request, "_http_request_id", None) or str(uuid.uuid4()).replace("-", "_")
        start_time = time.monotonic()

        page_lease = None
        observer_task = None
        state = None
        setup_success = False
        session = None

        max_retries = 3
        backoff_delays = [1.0, 2.0, 4.0]

        try:
            from app.services.browser.auth_manager import get_auth_manager
            from app.services.browser.auth_types import AuthStatus

            auth_mgr = get_auth_manager()
            if auth_mgr.coordination_lock.is_locked():
                raise HTTPException(status_code=503, detail="Authentication in progress.")

            if auth_mgr.refresh_playwright_status_lightweight() == AuthStatus.EXPIRED_SESSION:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication expired.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            engine = await self.hooks.get_browser_engine()
            session = await engine.get_session(self.hooks.session_name)
            browser_adapter = self.hooks.create_browser_adapter()
            for attempt in range(1, max_retries + 1):
                page_lease = None
                observer_task = None
                state = PlaywrightRequestState(request_id=request_id, start_time=start_time)

                def on_close(_page):
                    if state.cleanup_started:
                        return
                    logger.warning("Page close detected", extra={"request_id": state.request_id})
                    state.page_poisoned = True
                    if state.active_tab:
                        state.active_tab.invalidate()

                def on_crash(_page):
                    if state.cleanup_started:
                        return
                    logger.warning("Page crash detected", extra={"request_id": state.request_id})
                    state.page_poisoned = True
                    if state.active_tab:
                        state.active_tab.invalidate()

                state.on_close_handler = on_close
                state.on_crash_handler = on_crash

                try:
                    page_lease = await session.acquire_lease(
                        conversation_id=request.conversation_id,
                        request_id=state.request_id,
                    )
                    state.permit_acquired = True
                    page = page_lease.page

                    page.on("close", on_close)
                    page.on("crash", on_crash)

                    if page_lease.persistent_tab:
                        state.active_tab = page_lease.persistent_tab
                        state.reused_conversation = True
                        state.conversation_id = request.conversation_id

                    queue = asyncio.Queue(maxsize=100)

                    async def bridge_callback(_source, payload):
                        if state.cleanup_started:
                            return
                        if payload.get("type") == "ready":
                            state.js_ready.set()
                            return

                        if payload.get("type") in ("started", "chunk", "rewrite", "done"):
                            if not state.submission_confirmed.is_set():
                                state.submission_confirmed.set()

                        if payload.get("type") == "started":
                            return

                        try:
                            queue.put_nowait(payload)
                            state.max_queue_depth = max(state.max_queue_depth, queue.qsize())
                        except asyncio.QueueFull:
                            state.dropped_chunks += 1
                            state.queue_overflow = True
                            logger.error(
                                f"Queue overflow during streaming: {state.dropped_chunks} chunks dropped",
                                extra={
                                    "request_id": state.request_id,
                                    "dropped_chunks": state.dropped_chunks,
                                    "max_queue_depth": state.max_queue_depth,
                                },
                            )
                        except Exception as e:
                            logger.warning(f"Bridge emit failure: {e}")
                            state.page_poisoned = True

                    await session._setup_page_bridge(
                        page,
                        binding_name=self.hooks.callback_name,
                        callbacks_attr=self.hooks.bridge_callbacks_attr,
                    )
                    # Temporary Gemini-specific callback attribute may still be supplied
                    # by provider hooks for compatibility while PR3 finishes bridge cleanup.
                    getattr(page, self.hooks.bridge_callbacks_attr)[state.request_id] = bridge_callback
                    await self.hooks.sleep(0.01)

                    self._validate_tab_generation(
                        state.active_tab,
                        engine.browser_generation,
                        "Browser generation mismatch detected during prompt processing",
                    )
                    await self.hooks.navigate(page, state, request, self.config)
                    await self.hooks.wait_for_ready_ui(page, state, self.config)

                    if state.active_tab:
                        state.active_tab.heartbeat("auth_check")
                    self._validate_tab_generation(state.active_tab, engine.browser_generation)

                    grace_timeout = 2.5
                    poll_interval = 0.5
                    elapsed = 0.0
                    last_transient_error = None
                    is_authenticated = False
                    while elapsed < grace_timeout:
                        try:
                            is_authenticated = await browser_adapter.check_authentication(page)
                            if is_authenticated:
                                break
                        except TransientSessionError as e:
                            last_transient_error = e
                        await self.hooks.sleep(poll_interval)
                        elapsed += poll_interval

                    self._validate_tab_generation(state.active_tab, engine.browser_generation)
                    if not is_authenticated:
                        if last_transient_error:
                            raise last_transient_error
                        raise SessionNotAliveError("Authentication expired.")

                    if state.active_tab:
                        state.active_tab.heartbeat("observer_injection")
                    self._validate_tab_generation(state.active_tab, engine.browser_generation)
                    observer_task = asyncio.create_task(
                        self.hooks.start_observer(page, self.hooks.callback_name, state.request_id),
                        name=f"observer_{state.request_id}",
                    )

                    try:
                        async with self.hooks.timeout(5.0):
                            await state.js_ready.wait()
                    except asyncio.TimeoutError:
                        raise TransientSessionError("JS Ready Signal Timeout during pre-submission phase.")

                    break

                except TransientSessionError:
                    if page_lease or observer_task:
                        await self._cleanup(observer_task, state, page_lease, session)
                    if attempt == max_retries:
                        raise
                    await self.hooks.sleep(backoff_delays[attempt - 1])

            prompt = request.messages[-1].get("content", "")

            async with session.submit_lock:
                self._validate_tab_generation(state.active_tab, engine.browser_generation)
                await self.hooks.orchestrate_model_selection(browser_adapter, page, request.model, state)
                confirmed = await browser_adapter.submit_prompt(page, prompt, state)

            if not confirmed:
                raise HTTPException(status_code=500, detail=f"{self.hooks.provider_name.title()} failed to accept the prompt.")

            model = request.model or self.hooks.default_model
            if request.stream:
                setup_success = True
                logger.info(
                    f"Stream starting: {request_id}",
                    extra={
                        "request_id": request_id,
                        "conversation_id": state.conversation_id,
                        "provider": self.hooks.provider_name,
                        "model": request.model,
                    },
                )
                return StreamingResponse(
                    self._sse_generator(queue, model, page, state, observer_task, engine, session, page_lease),
                    media_type="text/event-stream",
                )

            resp = await self._get_buffered_response(queue, model, page, state, observer_task, engine, session, page_lease)
            setup_success = True
            return resp

        except asyncio.CancelledError:
            if state:
                logger.warning(f"Request cancelled: {state.request_id}", extra={"request_id": state.request_id})
            raise

        except Exception as e:
            poison_session_errors = (
                SessionNotAliveError,
                BrowserDisconnectedError,
                BrowserGenerationMismatchError,
            )
            if isinstance(e, poison_session_errors) and session:
                await session.handle_session_failure()

            correlation_headers = {}
            if state:
                correlation_headers["X-Request-ID"] = state.request_id

            if isinstance(e, SessionNotAliveError):
                detail = str(e) if str(e) else "Authentication expired."
                if "authentication expired" not in detail.lower():
                    detail = f"Authentication expired. {detail}"
                correlation_headers["WWW-Authenticate"] = "Bearer"
                logger.error(
                    f"Authentication failed: {detail}",
                    extra={"request_id": state.request_id if state else "unknown"} if state else {},
                )
                raise HTTPException(status_code=401, detail=detail, headers=correlation_headers)

            if isinstance(e, TransientSessionError):
                logger.error(f"Transient session error: {e}", extra={"request_id": state.request_id} if state else {})
                raise HTTPException(status_code=503, detail=str(e), headers=correlation_headers)

            if isinstance(e, (asyncio.TimeoutError, PlaywrightTimeoutError)):
                logger.error(f"Request timeout: {e}", extra={"request_id": state.request_id} if state else {})
                raise HTTPException(status_code=504, detail="Request timed out.", headers=correlation_headers)

            if isinstance(e, BrowserDisconnectedError):
                logger.error(f"Browser disconnected: {e}", extra={"request_id": state.request_id} if state else {})
                raise HTTPException(status_code=502, detail="Underlying browser process disconnected.", headers=correlation_headers)

            if isinstance(e, BrowserGenerationMismatchError):
                logger.error(f"Browser generation mismatch: {e}", extra={"request_id": state.request_id} if state else {})
                raise HTTPException(status_code=503, detail="Browser generation rollover mismatch.", headers=correlation_headers)

            if isinstance(e, PlaywrightError):
                if "closed" in str(e).lower():
                    logger.error(f"Browser session closed: {e}", extra={"request_id": state.request_id} if state else {})
                    raise HTTPException(status_code=503, detail="Browser session unavailable.", headers=correlation_headers)
                logger.error(f"Browser interaction failure: {e}", extra={"request_id": state.request_id} if state else {})
                raise HTTPException(status_code=502, detail="Browser interaction failure.", headers=correlation_headers)

            if isinstance(e, (BrowserShuttingDownError, LeaseInvalidatedError, QueueOverflowError, ConversationBusyError)):
                logger.error(f"Runtime error: {e}", extra={"request_id": state.request_id} if state else {})
                raise HTTPException(
                    status_code=503 if not isinstance(e, (LeaseInvalidatedError, ConversationBusyError)) else 409,
                    detail=str(e),
                    headers=correlation_headers,
                )

            if isinstance(e, HTTPException):
                raise

            logger.error(
                f"Unexpected error in {self.hooks.provider_name.title()} browser request executor: {e}",
                exc_info=True,
                extra={"request_id": state.request_id} if state else {},
            )
            raise HTTPException(status_code=500, detail="Internal server error.", headers=correlation_headers)

        finally:
            if not setup_success and (page_lease or observer_task):
                await self._cleanup(observer_task, state, page_lease, session)

    async def _sse_generator(
        self,
        queue: asyncio.Queue,
        model: str,
        page: Page,
        state: PlaywrightRequestState,
        observer_task: Optional[asyncio.Task],
        engine: Any,
        session: Any,
        lease: ManagedPage,
    ):
        from app.utils.streaming import format_sse_chunk, get_done_chunk

        request_id = state.request_id
        stream_start_time = time.monotonic()
        stream_cancelled = False

        try:
            if state.queue_overflow:
                logger.error(
                    f"Queue overflow detected at stream start: {state.dropped_chunks} chunks dropped",
                    extra={
                        "request_id": request_id,
                        "dropped_chunks": state.dropped_chunks,
                        "max_queue_depth": state.max_queue_depth,
                    },
                )
                raise QueueOverflowError("Event queue saturated")

            while True:
                try:
                    await self._register_conversation_if_available(page, state, session, lease)

                    payload = await asyncio.wait_for(queue.get(), timeout=self.config.chunk_timeout)
                    if payload.get("type") == "done":
                        break

                    if state.active_tab:
                        self._validate_tab_generation(state.active_tab, engine.browser_generation)

                    text_to_send = ""
                    if payload.get("type") == "chunk":
                        text_to_send = payload["delta"]
                    elif payload.get("type") == "rewrite" and not state.has_sent_text:
                        text_to_send = payload["full_text"]

                    if text_to_send:
                        state.has_sent_text = True
                        chunk = self.hooks.convert_to_openai_format(text_to_send, model, True)
                        if state.conversation_id:
                            chunk["conversation_id"] = state.conversation_id
                            chunk["reused_conversation"] = state.reused_conversation
                        yield await format_sse_chunk(chunk)
                except asyncio.TimeoutError:
                    break

            yield await get_done_chunk()

        except (asyncio.CancelledError, GeneratorExit):
            duration = time.monotonic() - stream_start_time
            logger.warning(
                f"Stream cancelled: {request_id}",
                extra={"request_id": request_id, "duration": f"{duration:.2f}s", "reason": "client_disconnect"},
            )
            stream_cancelled = True
            try:
                await self.hooks.stop_generation(page)
            except Exception:
                pass
            raise

        finally:
            if not stream_cancelled:
                duration = time.monotonic() - stream_start_time
                logger.info(
                    f"Stream completed: {request_id}",
                    extra={"request_id": request_id, "duration": f"{duration:.2f}s", "has_sent_text": state.has_sent_text},
                )
            await self._cleanup(observer_task, state, lease, session)

    async def _get_buffered_response(
        self,
        queue: asyncio.Queue,
        model: str,
        page: Page,
        state: PlaywrightRequestState,
        observer_task: Optional[asyncio.Task],
        engine: Any,
        session: Any,
        lease: ManagedPage,
    ):
        try:
            full_text = ""
            async with self.hooks.timeout(self.config.total_request_timeout):
                while True:
                    if state.queue_overflow:
                        raise QueueOverflowError("Event queue saturated")
                    await self._register_conversation_if_available(page, state, session, lease)

                    payload = await queue.get()
                    if payload.get("type") == "done":
                        break

                    if state.active_tab:
                        self._validate_tab_generation(state.active_tab, engine.browser_generation)

                    if payload.get("type") == "rewrite":
                        full_text = payload["full_text"]
                    if payload.get("type") == "chunk":
                        full_text += payload["delta"]

            res = self.hooks.convert_to_openai_format(full_text, model, False)
            if state.conversation_id:
                res["conversation_id"] = state.conversation_id
                res["reused_conversation"] = state.reused_conversation
            return res
        finally:
            await self._cleanup(observer_task, state, lease, session)

    async def _register_conversation_if_available(self, page: Page, state: PlaywrightRequestState, session: Any, lease: ManagedPage):
        if state.conversation_id or state.active_tab:
            return
        async with state.lock:
            if state.conversation_id or state.active_tab:
                return
            conversation_id = self.hooks.extract_conversation_id(page.url)
            if conversation_id:
                state.conversation_id = conversation_id
                state.active_tab = await session.register_conversation(conversation_id, lease)

    async def _cleanup(self, observer_task: Optional[asyncio.Task], state: Optional[PlaywrightRequestState], lease: Optional[ManagedPage], session: Any):
        if not state and not lease and not observer_task:
            return
        await asyncio.shield(self._do_cleanup(observer_task, state, lease, session))

    async def _do_cleanup(self, observer_task, state, lease, session):
        async with state.lock:
            if state.cleanup_started and state.page_closed:
                return
            state.cleanup_started = True
            try:
                if observer_task and not observer_task.done():
                    observer_task.cancel()
                    try:
                        await observer_task
                    except BaseException:
                        pass
                if lease:
                    callbacks = getattr(lease.page, self.hooks.bridge_callbacks_attr, None)
                    if callbacks is not None:
                        callbacks.pop(state.request_id, None)
                    if not state.page_closed:
                        try:
                            await self.hooks.stop_observer(lease.page, state.request_id)
                        except Exception as e:
                            logger.debug(
                                "BrowserRequestExecutor: stop_observer cleanup failed for request_id=%s: %s",
                                state.request_id,
                                e,
                            )
                        try:
                            if state.on_close_handler:
                                lease.page.remove_listener("close", state.on_close_handler)
                            if state.on_crash_handler:
                                lease.page.remove_listener("crash", state.on_crash_handler)
                        except Exception as e:
                            logger.debug(
                                "BrowserRequestExecutor: listener cleanup failed for request_id=%s: %s",
                                state.request_id,
                                e,
                            )
                    if not state.active_tab and not state.page_closed:
                        try:
                            conversation_id = self.hooks.extract_conversation_id(lease.page.url)
                            if conversation_id:
                                state.conversation_id = conversation_id
                                state.active_tab = await session.register_conversation(conversation_id, lease)
                        except Exception as e:
                            logger.debug(
                                "BrowserRequestExecutor: final conversation registration cleanup failed for request_id=%s: %s",
                                state.request_id,
                                e,
                            )
                    if state.page_poisoned and state.active_tab:
                        state.active_tab.invalidate()
                    await lease.close()
                    state.page_closed = True
            except Exception as e:
                logger.warning(
                    "BrowserRequestExecutor: cleanup encountered an unexpected error for request_id=%s: %s",
                    state.request_id if state else "unknown",
                    e,
                )

    def _validate_tab_generation(self, tab: Any, current_generation: int, message: Optional[str] = None):
        if tab:
            BrowserGenerationMismatchError.validate(tab.browser_generation, current_generation)
