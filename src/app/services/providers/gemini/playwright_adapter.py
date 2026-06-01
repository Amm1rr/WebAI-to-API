import asyncio
import uuid
import re
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from app.services.browser.engine import get_browser_engine
from app.services.browser.tab import ManagedPage, TabStatus
from app.services.browser.adapters.gemini_adapter import GeminiProviderAdapter
from app.services.browser.adapters.scripts.gemini_scripts import STREAM_EXTRACTOR_SCRIPT, STOP_OBSERVER_SCRIPT, SELECTORS
from app.services.browser.errors import (
    TransientSessionError,
    SessionNotAliveError,
    BrowserShuttingDownError,
    BrowserDisconnectedError,
    BrowserGenerationMismatchError,
    LeaseInvalidatedError,
    QueueOverflowError,
    ConversationBusyError
)
from app.services.providers.gemini.base_adapter import GeminiBackendAdapter
from app.services.providers.gemini.shared import convert_to_openai_format
from app.logger import logger
from app.config import CONFIG
from app.schemas.request import OpenAIChatRequest

@dataclass(frozen=True)
class PlaywrightAdapterConfig:
    """Consolidated configuration for the Playwright adapter."""
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
            total_request_timeout=CONFIG["Playwright"].getint("total_request_timeout", 120)
        )

@dataclass
class PlaywrightRequestState:
    """Shared state for a single request lifecycle in Playwright."""
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
    active_tab: Optional[Any] = None # PersistentTab
    js_ready: asyncio.Event = field(default_factory=asyncio.Event)
    submission_confirmed: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    has_sent_text: bool = False
    on_close_handler: Any = None
    on_crash_handler: Any = None
    queue_overflow: bool = False

class GeminiPlaywrightAdapter(GeminiBackendAdapter):
    """
    Backend adapter for Google Gemini using the Playwright browser native runtime.
    """
    
    def __init__(self, provider):
        self.provider = provider
        self.config = PlaywrightAdapterConfig.load()

    async def chat_completions(self, request: OpenAIChatRequest, cid: str, is_new_conversation: bool, tools_prompt: str) -> Any:
        # Use HTTP request_id if available (from middleware), otherwise generate
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
                    headers={"WWW-Authenticate": "Bearer"}
                )

            engine = await get_browser_engine()
            session = await engine.get_session("gemini")
            adapter = GeminiProviderAdapter(ui_wait_timeout=self.config.ui_wait_timeout)
            for attempt in range(1, max_retries + 1):
                page_lease = None
                observer_task = None
                state = PlaywrightRequestState(request_id=request_id, start_time=start_time)
                
                def on_close(p):
                    if state.cleanup_started: return
                    logger.warning("Page close detected", extra={"request_id": state.request_id})
                    state.page_poisoned = True
                    if state.active_tab: state.active_tab.invalidate()

                def on_crash(p):
                    if state.cleanup_started: return
                    logger.warning("Page crash detected", extra={"request_id": state.request_id})
                    state.page_poisoned = True
                    if state.active_tab: state.active_tab.invalidate()

                state.on_close_handler = on_close
                state.on_crash_handler = on_crash
                
                try:
                    # 1. Acquire Lease
                    page_lease = await session.acquire_lease(conversation_id=request.conversation_id, request_id=state.request_id)
                    state.permit_acquired = True
                    page = page_lease.page

                    page.on("close", on_close)
                    page.on("crash", on_crash)
                    
                    if page_lease.persistent_tab:
                        state.active_tab = page_lease.persistent_tab
                        state.reused_conversation = True
                        state.conversation_id = request.conversation_id
                    
                    def check_generation():
                        self._validate_tab_generation(state.active_tab, engine.browser_generation, "Browser generation mismatch detected during prompt processing")

                    callback_name = "__gemini_bridge"
                    queue = asyncio.Queue(maxsize=100)

                    async def bridge_callback(source, payload):
                        if state.cleanup_started: return
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
                            # Log queue overflow event (silent data loss detection)
                            logger.error(
                                f"Queue overflow during streaming: {state.dropped_chunks} chunks dropped",
                                extra={"request_id": state.request_id, "dropped_chunks": state.dropped_chunks, "max_queue_depth": state.max_queue_depth}
                            )
                        except Exception as e:
                            logger.warning(f"Bridge emit failure: {e}")
                            state.page_poisoned = True

                    await session._setup_page_bridge(page)
                    page._gemini_callbacks[state.request_id] = bridge_callback
                    await asyncio.sleep(0.01)
                    
                    # 2. Target Navigation
                    check_generation()
                    if state.reused_conversation:
                        target_url = f"https://gemini.google.com/app/{state.conversation_id}"
                        if page.url != target_url:
                            if state.active_tab: state.active_tab.heartbeat("navigation_start")
                            await page.goto(target_url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout)
                            if state.active_tab: state.active_tab.heartbeat("navigation_end")
                    elif request.conversation_id:
                        if state.active_tab: state.active_tab.heartbeat("navigation_start")
                        await page.goto(f"https://gemini.google.com/app/{request.conversation_id}", wait_until="domcontentloaded", timeout=self.config.navigation_timeout)
                        if state.active_tab: state.active_tab.heartbeat("navigation_end")
                    else:
                        if "gemini.google.com/app" not in page.url:
                            if state.active_tab: state.active_tab.heartbeat("navigation_start")
                            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=self.config.navigation_timeout)
                            if state.active_tab: state.active_tab.heartbeat("navigation_end")
                    
                    input_locator = page.locator(SELECTORS["INPUT"]).first
                    if state.active_tab: state.active_tab.heartbeat("input_wait")
                    try:
                        check_generation()
                        await input_locator.wait_for(state="visible", timeout=self.config.ui_wait_timeout)
                        await asyncio.sleep(0.5)
                    except (PlaywrightTimeoutError, PlaywrightError) as e:
                        state.page_poisoned = True
                        if state.active_tab:
                            state.active_tab.status = TabStatus.DEAD
                        raise TransientSessionError(f"Gemini input textbox acquisition failed: {e}") from e
                    
                    # 3. Outer Orchestration: Redirect grace period check
                    if state.active_tab: state.active_tab.heartbeat("auth_check")
                    check_generation()
                    
                    grace_timeout = 2.5
                    poll_interval = 0.5
                    elapsed = 0.0
                    last_transient_error = None
                    is_authenticated = False
                    while elapsed < grace_timeout:
                        try:
                            is_authenticated = await adapter.check_authentication(page)
                            if is_authenticated:
                                break
                        except TransientSessionError as e:
                            last_transient_error = e
                        await asyncio.sleep(poll_interval)
                        elapsed += poll_interval
                        
                    check_generation()
                    if not is_authenticated:
                        if last_transient_error:
                            raise last_transient_error
                        raise SessionNotAliveError("Authentication expired.")

                    # 4. Observer Injection
                    if state.active_tab: state.active_tab.heartbeat("observer_injection")
                    check_generation()
                    observer_task = asyncio.create_task(
                        page.evaluate(f"({STREAM_EXTRACTOR_SCRIPT})('{callback_name}', '{state.request_id}')"),
                        name=f"observer_{state.request_id}"
                    )

                    try:
                        async with asyncio.timeout(5.0):
                            await state.js_ready.wait()
                    except asyncio.TimeoutError:
                        raise TransientSessionError("JS Ready Signal Timeout during pre-submission phase.")
                    
                    break
                    
                except TransientSessionError as e:
                    if page_lease or observer_task:
                        await self._cleanup(observer_task, state, page_lease, session)
                    if attempt == max_retries:
                        raise
                    await asyncio.sleep(backoff_delays[attempt - 1])

            # 5. Hardened Prompt Submission
            prompt = request.messages[-1].get("content", "")
            confirmed = False

            async with session.submit_lock:
                check_generation()
                confirmed = await adapter.submit_prompt(page, prompt, state)

            if not confirmed:
                raise HTTPException(status_code=500, detail="Gemini failed to accept the prompt.")
            
            is_stream = request.stream if request.stream is not None else False
            if is_stream:
                setup_success = True
                logger.info(
                    f"Stream starting: {request_id}",
                    extra={
                        "request_id": request_id,
                        "conversation_id": state.conversation_id,
                        "provider": "gemini",
                        "model": request.model
                    }
                )
                return StreamingResponse(
                    self._sse_generator(queue, request.model or "playwright/gemini", page, state, observer_task, engine, session, page_lease),
                    media_type="text/event-stream"
                )
            else:
                resp = await self._get_buffered_response(queue, request.model or "playwright/gemini", page, state, observer_task, engine, session, page_lease)
                setup_success = True
                return resp

        except asyncio.CancelledError:
            # Log cancellation for observability
            if state:
                logger.warning(f"Request cancelled: {state.request_id}", extra={"request_id": state.request_id})
            raise

        except Exception as e:
            # Map internal Playwright and provider errors to appropriate HTTP status codes
            poison_session_errors = (SessionNotAliveError, BrowserDisconnectedError, BrowserGenerationMismatchError)
            if isinstance(e, poison_session_errors) and session:
                await session.handle_session_failure()

            # Build correlation header for error responses
            correlation_headers = {}
            if state:
                correlation_headers["X-Request-ID"] = state.request_id

            if isinstance(e, SessionNotAliveError):
                detail = str(e) if str(e) else "Authentication expired."
                if "authentication expired" not in detail.lower():
                    detail = f"Authentication expired. {detail}"
                correlation_headers["WWW-Authenticate"] = "Bearer"
                logger.error(f"Authentication failed: {detail}", extra={"request_id": state.request_id if state else "unknown"} if state else {})
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
                raise HTTPException(status_code=503 if not isinstance(e, (LeaseInvalidatedError, ConversationBusyError)) else 409, detail=str(e), headers=correlation_headers)

            if isinstance(e, HTTPException): raise

            logger.error(f"Unexpected error in GeminiPlaywrightAdapter: {e}", exc_info=True, extra={"request_id": state.request_id} if state else {})
            raise HTTPException(status_code=500, detail="Internal server error.", headers=correlation_headers)

        finally:
            if not setup_success:
                if page_lease or observer_task:
                    await self._cleanup(observer_task, state, page_lease, session)

    async def _sse_generator(self, queue: asyncio.Queue, model: str, page: Page, state: PlaywrightRequestState, observer_task: Optional[asyncio.Task], engine: Any, session: Any, lease: ManagedPage):
        from app.utils.streaming import format_sse_chunk, get_done_chunk
        request_id = state.request_id
        stream_start_time = time.monotonic()
        stream_cancelled = False

        try:
            # Check for queue overflow at stream start (silent data loss detection)
            if state.queue_overflow:
                logger.error(
                    f"Queue overflow detected at stream start: {state.dropped_chunks} chunks dropped",
                    extra={"request_id": request_id, "dropped_chunks": state.dropped_chunks, "max_queue_depth": state.max_queue_depth}
                )
                raise QueueOverflowError("Event queue saturated")

            while True:
                try:
                    if not state.conversation_id and not state.active_tab:
                        async with state.lock:
                            if not state.conversation_id and not state.active_tab:
                                match = re.search(r"/app/([a-z0-9]+)", page.url)
                                if match:
                                    state.conversation_id = match.group(1)
                                    state.active_tab = await session.register_conversation(state.conversation_id, lease)

                    payload = await asyncio.wait_for(queue.get(), timeout=self.config.chunk_timeout)
                    if payload.get("type") == "done": break

                    if state.active_tab:
                        self._validate_tab_generation(state.active_tab, engine.browser_generation)

                    text_to_send = ""
                    if payload.get("type") == "chunk": text_to_send = payload["delta"]
                    elif payload.get("type") == "rewrite" and not state.has_sent_text: text_to_send = payload["full_text"]

                    if text_to_send:
                        state.has_sent_text = True
                        chunk = convert_to_openai_format(text_to_send, model, stream=True)
                        if state.conversation_id:
                            chunk["conversation_id"] = state.conversation_id
                            chunk["reused_conversation"] = state.reused_conversation
                        yield await format_sse_chunk(chunk)
                except asyncio.TimeoutError: break

            yield await get_done_chunk()

        except (asyncio.CancelledError, GeneratorExit):
            # Stream cancellation logging (only for cancellation, not completion)
            duration = time.monotonic() - stream_start_time
            logger.warning(
                f"Stream cancelled: {request_id}",
                extra={"request_id": request_id, "duration": f"{duration:.2f}s", "reason": "client_disconnect"}
            )
            stream_cancelled = True
            try:
                stop_button = page.locator(SELECTORS["STOP_BUTTON"]).first
                if await stop_button.is_visible(): await stop_button.click()
            except: pass
            raise

        finally:
            # Stream completion logging (only for successful completion)
            if not stream_cancelled:
                duration = time.monotonic() - stream_start_time
                logger.info(
                    f"Stream completed: {request_id}",
                    extra={"request_id": request_id, "duration": f"{duration:.2f}s", "has_sent_text": state.has_sent_text}
                )
            await self._cleanup(observer_task, state, lease, session)

    async def _get_buffered_response(self, queue: asyncio.Queue, model: str, page: Page, state: PlaywrightRequestState, observer_task: Optional[asyncio.Task], engine: Any, session: Any, lease: ManagedPage):
        try:
            full_text = ""
            async with asyncio.timeout(self.config.total_request_timeout):
                while True:
                    if state.queue_overflow: raise QueueOverflowError("Event queue saturated")
                    if not state.conversation_id and not state.active_tab:
                        async with state.lock:
                            if not state.conversation_id and not state.active_tab:
                                match = re.search(r"/app/([a-z0-9]+)", page.url)
                                if match:
                                    state.conversation_id = match.group(1)
                                    state.active_tab = await session.register_conversation(state.conversation_id, lease)

                    payload = await queue.get()
                    if payload.get("type") == "done": break
                    
                    if state.active_tab:
                        self._validate_tab_generation(state.active_tab, engine.browser_generation)

                    if payload.get("type") == "rewrite": full_text = payload["full_text"]
                    if payload.get("type") == "chunk": full_text += payload["delta"]
            
            res = convert_to_openai_format(full_text, model, stream=False)
            if state.conversation_id:
                res["conversation_id"] = state.conversation_id
                res["reused_conversation"] = state.reused_conversation
            return res
        finally:
            await self._cleanup(observer_task, state, lease, session)

    async def _cleanup(self, observer_task: Optional[asyncio.Task], state: Optional[PlaywrightRequestState], lease: Optional[ManagedPage], session: Any):
        if not state and not lease and not observer_task: return
        await asyncio.shield(self._do_cleanup(observer_task, state, lease, session))

    async def _do_cleanup(self, observer_task, state, lease, session):
        async with state.lock:
            if state.cleanup_started and state.page_closed: return
            state.cleanup_started = True
            try:
                if observer_task and not observer_task.done():
                    observer_task.cancel()
                    try: await observer_task
                    except: pass
                if lease:
                    if hasattr(lease.page, "_gemini_callbacks"):
                        lease.page._gemini_callbacks.pop(state.request_id, None)
                    if not state.page_closed:
                        try: await lease.page.evaluate(f"({STOP_OBSERVER_SCRIPT})('{state.request_id}')")
                        except: pass
                        try:
                            if state.on_close_handler: lease.page.remove_listener("close", state.on_close_handler)
                            if state.on_crash_handler: lease.page.remove_listener("crash", state.on_crash_handler)
                        except: pass
                    if not state.active_tab and not state.page_closed:
                        try:
                            match = re.search(r"/app/([a-z0-9]+)", lease.page.url)
                            if match:
                                state.conversation_id = match.group(1)
                                state.active_tab = await session.register_conversation(state.conversation_id, lease)
                        except: pass
                    if state.page_poisoned and state.active_tab: state.active_tab.invalidate()
                    await lease.close()
                    state.page_closed = True
            except: pass

    def _validate_tab_generation(self, tab: Any, current_generation: int, message: Optional[str] = None):
        if tab:
            BrowserGenerationMismatchError.validate(tab.browser_generation, current_generation)

    async def close(self) -> None: pass
