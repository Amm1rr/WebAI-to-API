import asyncio
import os
import time
import uuid
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from app.services.base import BaseProvider
from app.services.browser.engine import get_browser_engine
from app.services.browser.tab import ManagedPage, PersistentTab, TabStatus
from app.services.browser.adapters.gemini_adapter import GeminiProviderAdapter
from app.schemas.request import OpenAIChatRequest
from app.logger import logger
from app.config import CONFIG
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

@dataclass
class RequestState:
    """Shared state for a single request lifecycle."""
    request_id: str
    start_time: float # Monotonic for TTFT
    permit_acquired: bool = False
    cleanup_started: bool = False
    page_closed: bool = False
    page_poisoned: bool = False
    dropped_chunks: int = 0
    max_queue_depth: int = 0
    conversation_id: Optional[str] = None
    reused_conversation: bool = False
    active_tab: Optional[PersistentTab] = None
    js_ready: asyncio.Event = field(default_factory=asyncio.Event)
    submission_confirmed: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    has_sent_text: bool = False
    on_close_handler: Any = None
    on_crash_handler: Any = None
    queue_overflow: bool = False

class GeminiPlaywrightProvider(BaseProvider):
    """
    Production-grade Browser-native provider for Gemini Web.
    
    Features: 
    - Decoupled Persistent Conversations (Leasing model)
    - Concurrency-safe Registry
    - Ready-Signal Synchronization
    - Multi-layer Prompt Submission
    - Session-wide Submit Serialization (submit_lock)
    """

    async def chat_completions(self, request: OpenAIChatRequest) -> Any:
        request_id = str(uuid.uuid4()).replace("-", "_")
        start_time = time.monotonic()
        
        page_lease = None
        observer_task = None
        state = None
        setup_success = False
        session = None
        
        max_retries = 3
        backoff_delays = [1.0, 2.0, 4.0]
        
        try:
            from app.services.browser.auth_manager import get_auth_manager, LoginState, AuthStatus
            auth_mgr = get_auth_manager()
            if auth_mgr.login_state == LoginState.LOGIN_IN_PROGRESS:
                raise HTTPException(status_code=503, detail="Authentication in progress.")
            
            if auth_mgr.refresh_playwright_status_lightweight() == AuthStatus.EXPIRED_SESSION:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication expired.",
                    headers={"WWW-Authenticate": "Bearer"}
                )

            engine = await get_browser_engine()
            session = await engine.get_session("gemini")
            adapter = GeminiProviderAdapter()
            for attempt in range(1, max_retries + 1):
                page_lease = None
                observer_task = None
                state = RequestState(request_id=request_id, start_time=start_time)
                
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

                # Bind handlers to state for cleanup-layer access
                state.on_close_handler = on_close
                state.on_crash_handler = on_crash
                
                try:
                    # 1. Acquire Lease (Request-scoped semaphore + Tab selection)
                    page_lease = await session.acquire_lease(conversation_id=request.conversation_id, request_id=state.request_id)
                    state.permit_acquired = True
                    page = page_lease.page

                    # Register lifecycle listeners to detect poisoned tabs
                    page.on("close", on_close)
                    page.on("crash", on_crash)
                    
                    # If the lease returned a persistent tab, track it in state
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
                        
                        # Authoritative submission confirmation
                        if payload.get("type") in ("started", "chunk", "rewrite", "done"):
                            if not state.submission_confirmed.is_set():
                                state.submission_confirmed.set()

                        # 'started' is a synchronization-only signal; skip queueing.
                        if payload.get("type") == "started":
                            return 
                            
                        try:
                            queue.put_nowait(payload)
                            state.max_queue_depth = max(state.max_queue_depth, queue.qsize())
                        except asyncio.QueueFull:
                            state.dropped_chunks += 1
                            state.queue_overflow = True
                        except Exception as e:
                            logger.warning(f"Bridge emit failure: {e}")
                            state.page_poisoned = True

                    # Ensure permanent bridge is exposed on this page and register callback
                    await session._setup_page_bridge(page)
                    page._gemini_callbacks[state.request_id] = bridge_callback
                    
                    # Cooperative yield to ensure event loop schedules registration
                    await asyncio.sleep(0.01)
                    
                    logger.debug(
                        f"Bridge callback registered for requestId: {state.request_id}",
                        extra={"request_id": state.request_id, "exposed_keys": list(page._gemini_callbacks.keys())}
                    )
                    
                    nav_timeout = CONFIG["Playwright"].getint("navigation_timeout", 30000)
                    
                    # 2. Target Navigation
                    check_generation()
                    if state.reused_conversation:
                        target_url = f"https://gemini.google.com/app/{state.conversation_id}"
                        if page.url != target_url:
                            if state.active_tab: state.active_tab.heartbeat("navigation_start")
                            await page.goto(target_url, wait_until="domcontentloaded", timeout=nav_timeout)
                            if state.active_tab: state.active_tab.heartbeat("navigation_end")
                    elif request.conversation_id:
                        if state.active_tab: state.active_tab.heartbeat("navigation_start")
                        await page.goto(f"https://gemini.google.com/app/{request.conversation_id}", wait_until="domcontentloaded", timeout=nav_timeout)
                        if state.active_tab: state.active_tab.heartbeat("navigation_end")
                    else:
                        if "gemini.google.com/app" not in page.url:
                            if state.active_tab: state.active_tab.heartbeat("navigation_start")
                            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=nav_timeout)
                            if state.active_tab: state.active_tab.heartbeat("navigation_end")
                    
                    input_locator = page.locator(SELECTORS["INPUT"]).first
                    if state.active_tab: state.active_tab.heartbeat("input_wait")
                    try:
                        check_generation()
                        await input_locator.wait_for(state="visible", timeout=15000)
                        # Let the heavy framework JS event listeners settle completely
                        await asyncio.sleep(0.5)
                    except (PlaywrightTimeoutError, PlaywrightError) as e:
                        state.page_poisoned = True
                        if state.active_tab:
                            state.active_tab.status = TabStatus.DEAD
                        raise TransientSessionError(f"Gemini input textbox acquisition failed: {e}") from e
                    
                    # 3. Outer Orchestration: Redirect grace period check (2.5s)
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
                            logger.debug(f"Transient authentication check failed during grace period: {e}")
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

                    # Sync: Wait for JS Ready
                    try:
                        async with asyncio.timeout(5.0):
                            await state.js_ready.wait()
                    except asyncio.TimeoutError:
                        logger.warning("JS Ready Signal Timeout", extra={"request_id": state.request_id})
                        raise TransientSessionError("JS Ready Signal Timeout during pre-submission phase.")
                    
                    # Successful pre-submission setup: break out of the retry loop
                    break
                    
                except TransientSessionError as e:
                    logger.warning(
                        f"Transient failure during pre-submission phase (Attempt {attempt}/{max_retries}): {e}",
                        extra={"request_id": state.request_id}
                    )
                    # IMMEDIATELY release lease and clean up resources before backoff sleep
                    if page_lease or observer_task:
                        await self._cleanup(observer_task, state, page_lease, session)
                        page_lease = None
                        observer_task = None
                    
                    if attempt == max_retries:
                        logger.error(
                            "Max retries exhausted for transient pre-submission failures.",
                            extra={"request_id": state.request_id}
                        )
                        raise
                    
                    backoff_delay = backoff_delays[attempt - 1]
                    await asyncio.sleep(backoff_delay)

            # 5. Hardened Prompt Submission Boundary (Serialized via submit_lock - Strictly non-retryable)
            prompt = request.messages[-1].get("content", "")
            confirmed = False

            async with session.submit_lock:
                logger.debug("Submit lock acquired", extra={"request_id": state.request_id})
                check_generation()
                confirmed = await adapter.submit_prompt(page, prompt, state)
                logger.debug("Submit lock released", extra={"request_id": state.request_id})

            if not confirmed:
                raise HTTPException(status_code=500, detail="Gemini failed to accept the prompt.")
            
            logger.info("Prompt submitted", extra={"request_id": state.request_id})

            is_stream = request.stream if request.stream is not None else False
            if is_stream:
                setup_success = True
                return StreamingResponse(
                    self._sse_generator(queue, request.model or "playwright/gemini", page, state, observer_task, engine, session, page_lease),
                    media_type="text/event-stream"
                )
            else:
                resp = await self._get_buffered_response(queue, request.model or "playwright/gemini", page, state, observer_task, engine, session, page_lease)
                setup_success = True
                return resp

        except asyncio.CancelledError:
            raise
        except Exception as e:
            req_id = state.request_id if state else request_id
            expected_recoverable_errors = (
                SessionNotAliveError,
                BrowserGenerationMismatchError,
                TransientSessionError,
            )
            if isinstance(e, expected_recoverable_errors):
                logger.warning(
                    "Recoverable chat_completions failure: %s",
                    e,
                    extra={"request_id": req_id},
                )
            elif isinstance(e, BrowserDisconnectedError):
                logger.exception("Browser disconnected during chat_completions", extra={"request_id": req_id})
            else:
                logger.exception("Error in chat_completions", extra={"request_id": req_id})
            from playwright.async_api import Error as PlaywrightError
            
            poison_session_errors = (
                SessionNotAliveError,
                BrowserDisconnectedError,
                BrowserGenerationMismatchError,
            )
            if isinstance(e, poison_session_errors) and session:
                await session.handle_session_failure()
            
            if isinstance(e, SessionNotAliveError):
                try:
                    from app.services.browser.auth_manager import get_auth_manager
                    get_auth_manager().mark_expired()
                except Exception:
                    pass
                raise HTTPException(
                    status_code=401,
                    detail="Authentication expired.",
                    headers={"WWW-Authenticate": "Bearer"}
                )
            if isinstance(e, TransientSessionError):
                raise HTTPException(status_code=503, detail=str(e))
            if isinstance(e, asyncio.TimeoutError):
                raise HTTPException(status_code=504, detail="Request timed out.")
            if isinstance(e, PlaywrightError):
                err_msg = str(e).lower()
                if "target page, context or browser has been closed" in err_msg or "execution context was destroyed" in err_msg:
                    raise HTTPException(status_code=503, detail="Browser session unavailable.")
                raise HTTPException(status_code=502, detail="Browser interaction failure.")
            if isinstance(e, BrowserShuttingDownError):
                raise HTTPException(status_code=503, detail="Browser engine is shutting down.")
            if isinstance(e, BrowserDisconnectedError):
                raise HTTPException(status_code=502, detail="Underlying browser process disconnected.")
            if isinstance(e, BrowserGenerationMismatchError):
                raise HTTPException(status_code=503, detail="Browser generation rollover mismatch.")
            if isinstance(e, LeaseInvalidatedError):
                raise HTTPException(status_code=409, detail="Tab lease has been invalidated.")
            if isinstance(e, QueueOverflowError):
                raise HTTPException(status_code=429, detail="Event queue saturated.")
            if isinstance(e, ConversationBusyError):
                raise HTTPException(status_code=409, detail="Conversation is busy with another active request.")
            if isinstance(e, HTTPException): raise
            raise HTTPException(status_code=500, detail="Internal server error.")
        finally:
            # Fix Leak: If setup was cancelled or failed before returning/awaiting, cleanup now.
            if not setup_success:
                if page_lease or observer_task:
                    await self._cleanup(observer_task, state, page_lease, session)
                    page_lease = None
                    observer_task = None

    async def _sse_generator(self, queue: asyncio.Queue, model: str, page: Page, state: RequestState, observer_task: Optional[asyncio.Task], engine: Any, session: Any, lease: ManagedPage):
        """Streaming generator with lazy conversation registration."""
        from app.utils.streaming import format_sse_chunk, get_done_chunk
        chunk_timeout = CONFIG["Playwright"].getint("chunk_timeout", 90)
        first_token_time = None
        
        try:
            while True:
                if state.queue_overflow:
                    from app.services.browser.errors import QueueOverflowError
                    raise QueueOverflowError("Event queue saturated")
                try:
                    if not state.conversation_id and not state.active_tab:
                        async with state.lock:
                            if not state.conversation_id and not state.active_tab:
                                match = re.search(r"/app/([a-z0-9]+)", page.url)
                                if match:
                                    state.conversation_id = match.group(1)
                                    state.active_tab = await session.register_conversation(state.conversation_id, lease)
                                    state.active_tab.heartbeat("cleanup_id_found")

                    payload = await asyncio.wait_for(queue.get(), timeout=chunk_timeout)
                    if payload.get("type") == "done": break
                    
                    if state.active_tab:
                        self._validate_tab_generation(state.active_tab, engine.browser_generation, "Browser generation rollover detected during streaming")
                        state.active_tab.heartbeat("streaming_progress")

                    text_to_send = ""
                    if payload.get("type") == "chunk":
                        text_to_send = payload["delta"]
                    elif payload.get("type") == "rewrite" and not state.has_sent_text:
                        text_to_send = payload["full_text"]

                    if text_to_send:
                        if not first_token_time:
                            first_token_time = time.monotonic()
                            logger.info("Stream started", extra={"ttft": f"{first_token_time - state.start_time:.2f}s", "request_id": state.request_id})
                        state.has_sent_text = True
                        chunk = self._convert_to_openai_format(text_to_send, model, stream=True, state=state)
                        yield await format_sse_chunk(chunk)
                except asyncio.TimeoutError: break
            yield await get_done_chunk()
        except (asyncio.CancelledError, GeneratorExit):
            try:
                stop_button = page.locator(SELECTORS["STOP_BUTTON"]).first
                if await stop_button.is_visible(): await stop_button.click()
            except: pass
            raise
        except Exception as e:
            logger.exception("Error in streaming response generator", extra={"request_id": state.request_id})
            raise
        finally:
            if lease or observer_task:
                await self._cleanup(observer_task, state, lease, session)
                lease = None
                observer_task = None

    async def _get_buffered_response(self, queue: asyncio.Queue, model: str, page: Page, state: RequestState, observer_task: Optional[asyncio.Task], engine: Any, session: Any, lease: ManagedPage):
        """Full response buffer."""
        total_timeout = CONFIG["Playwright"].getint("total_request_timeout", 120)
        try:
            full_text = ""
            async with asyncio.timeout(total_timeout):
                while True:
                    if state.queue_overflow:
                        from app.services.browser.errors import QueueOverflowError
                        raise QueueOverflowError("Event queue saturated")
                    if not state.conversation_id and not state.active_tab:
                        async with state.lock:
                            if not state.conversation_id and not state.active_tab:
                                match = re.search(r"/app/([a-z0-9]+)", page.url)
                                if match:
                                    state.conversation_id = match.group(1)
                                    state.active_tab = await session.register_conversation(state.conversation_id, lease)
                                    state.active_tab.heartbeat("cleanup_id_found")

                    payload = await queue.get()
                    if payload.get("type") == "done": break
                    
                    if state.active_tab:
                        self._validate_tab_generation(state.active_tab, engine.browser_generation, "Browser generation rollover detected during buffering")
                        state.active_tab.heartbeat("buffering_progress")

                    if payload.get("type") == "rewrite": full_text = payload["full_text"]
                    if payload.get("type") == "chunk": full_text += payload["delta"]
            return self._convert_to_openai_format(full_text, model, stream=False, state=state)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Request timed out.")
        finally:
            if lease or observer_task:
                await self._cleanup(observer_task, state, lease, session)
                lease = None
                observer_task = None

    async def _cleanup(self, observer_task: Optional[asyncio.Task], state: Optional[RequestState], lease: Optional[ManagedPage], session: Any):
        """Deterministic release of request resources."""
        if not state and not lease and not observer_task:
            return
        if not state:
            state = RequestState(request_id="unknown", start_time=time.monotonic())
        # Use shield to ensure cleanup completes even if outer request is cancelled
        await asyncio.shield(self._do_cleanup(observer_task, state, lease, session))

    async def _do_cleanup(self, observer_task: Optional[asyncio.Task], state: RequestState, lease: Optional[ManagedPage], session: Any):
        """Actual cleanup implementation, shielded from cancellation.

        Runtime safety ordering guarantees:
        1. observer cancel: Cancel and await Python observer_task, then destroy JS observer.
        2. listener removal: Remove request-level page close/crash event listeners.
        3. lease release: Release ManagedPage lease, invalidating if poisoned.
        4. semaphore release: ManagedPage close delegates to final session semaphore release.
        """
        async with state.lock:
            if state.cleanup_started and state.page_closed: return
            state.cleanup_started = True
            
            try:
                # 1. Cancel Python observer task
                if observer_task and not observer_task.done():
                    observer_task.cancel()
                    try: await observer_task
                    except: pass
                
                # Clean up our request callback from page registry
                if lease and hasattr(lease.page, "_gemini_callbacks"):
                    lease.page._gemini_callbacks.pop(state.request_id, None)
                
                # 2. Force JS observer destruction (Request-scoped)
                if lease and not state.page_closed:
                    try:
                        await lease.page.evaluate(f"({STOP_OBSERVER_SCRIPT})('{state.request_id}')")
                    except Exception as e:
                        logger.debug(f"JS Cleanup binding failed: {e}")
                        state.page_poisoned = True

                # 2.5. Defensive listener removal (Absolute lifecycle protection)
                if lease and not state.page_closed:
                    try:
                        if state.on_close_handler:
                            lease.page.remove_listener("close", state.on_close_handler)
                        if state.on_crash_handler:
                            lease.page.remove_listener("crash", state.on_crash_handler)
                    except: pass

                # 3. Final URL probe to salvage conversation ID
                if not state.active_tab and lease and not state.page_closed:
                    try:
                        match = re.search(r"/app/([a-z0-9]+)", lease.page.url)
                        if match:
                            state.conversation_id = match.group(1)
                            state.active_tab = await session.register_conversation(state.conversation_id, lease)
                    except: pass

                # 4. Release Lease
                if lease:
                    # If page was poisoned (crash/close/binding-fail), ManagedPage.close() 
                    # must ensure it doesn't return a bad tab to idle pool.
                    if state.page_poisoned and state.active_tab:
                        state.active_tab.invalidate()
                    
                    await lease.close()
                    state.page_closed = True
                    if state.active_tab:
                        logger.info(f"Lease returned for CID: {state.conversation_id}", extra={"request_id": state.request_id})
            except Exception as e:
                logger.warning(f"Cleanup Error: {e}", extra={"request_id": state.request_id})

    def _validate_tab_generation(self, tab: Any, current_generation: int, context_msg: str = "Browser generation mismatch detected"):
        if tab:
            from app.services.browser.errors import BrowserGenerationMismatchError
            BrowserGenerationMismatchError.validate(tab.browser_generation, current_generation, context_msg)

    async def list_models(self) -> List[dict]:
        return [{"id": "playwright/gemini", "object": "model", "created": int(time.time()), "owned_by": "google-playwright"}]

    async def close(self) -> None: pass

    def _convert_to_openai_format(self, text: str, model: str, stream: bool, state: RequestState):
        ts = int(time.time())
        choice_key = "delta" if stream else "message"
        content = {"role": "assistant", "content": text}
        res = {
            "id": f"chatcmpl-{ts}",
            "object": "chat.completion.chunk" if stream else "chat.completion",
            "created": ts,
            "model": model,
            "choices": [{"index": 0, choice_key: content, "finish_reason": "stop" if not stream else None}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        if state.conversation_id:
            res["conversation_id"] = state.conversation_id
            res["reused_conversation"] = state.reused_conversation
        return res
