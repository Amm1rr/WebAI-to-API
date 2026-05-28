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
from app.services.browser.engine import get_browser_engine, ManagedPage, PersistentTab
from app.schemas.request import OpenAIChatRequest
from app.logger import logger
from app.config import CONFIG
from .gemini_playwright_scripts import STREAM_EXTRACTOR_SCRIPT, SELECTORS

@dataclass
class RequestState:
    """Shared state for a single request lifecycle."""
    request_id: str
    start_time: float # Monotonic for TTFT
    permit_acquired: bool = False
    cleanup_started: bool = False
    page_closed: bool = False
    dropped_chunks: int = 0
    max_queue_depth: int = 0
    conversation_id: Optional[str] = None
    reused_conversation: bool = False
    active_tab: Optional[PersistentTab] = None
    js_ready: asyncio.Event = field(default_factory=asyncio.Event)
    submission_confirmed: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    has_sent_text: bool = False

class GeminiPlaywrightProvider(BaseProvider):
    """
    Production-grade Browser-native provider for Gemini Web.
    
    Features: 
    - Decoupled Persistent Conversations (Leasing model)
    - Concurrency-safe Registry
    - Ready-Signal Synchronization
    - Multi-layer Prompt Submission
    """

    async def chat_completions(self, request: OpenAIChatRequest) -> Any:
        engine = await get_browser_engine()
        state = RequestState(request_id=str(uuid.uuid4()).replace("-", "_"), start_time=time.monotonic())
        session = await engine.get_session("gemini")
        
        page_lease = None
        observer_task = None

        try:
            # 1. Acquire Lease (Request-scoped semaphore + Tab selection)
            # This handles both conversational reuse and fresh tab creation.
            page_lease = await session.acquire_lease(conversation_id=request.conversation_id, request_id=state.request_id)
            state.permit_acquired = True
            page = page_lease.page
            
            # If the lease returned a persistent tab, track it in state
            if page_lease.persistent_tab:
                state.active_tab = page_lease.persistent_tab
                state.reused_conversation = True
                state.conversation_id = request.conversation_id
            
            callback_name = f"emit_{state.request_id}"
            queue = asyncio.Queue(maxsize=100)

            async def bridge_callback(source, payload):
                if state.cleanup_started: return
                if payload.get("type") == "ready":
                    state.js_ready.set()
                    return
                
                # Authoritative submission confirmation: 
                # only first transition from (not confirmed -> confirmed) matters.
                if payload.get("type") in ("started", "chunk", "rewrite"):
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

            await page.expose_binding(callback_name, bridge_callback)
            
            nav_timeout = CONFIG["Playwright"].getint("navigation_timeout", 30000)
            
            # 2. Target Navigation
            if state.reused_conversation:
                target_url = f"https://gemini.google.com/app/{state.conversation_id}"
                if page.url != target_url:
                    if state.active_tab: state.active_tab.heartbeat("navigation_start")
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=nav_timeout)
                    if state.active_tab: state.active_tab.heartbeat("navigation_end")
            elif request.conversation_id:
                # Cold-start of a specific conversation ID
                if state.active_tab: state.active_tab.heartbeat("navigation_start")
                await page.goto(f"https://gemini.google.com/app/{request.conversation_id}", wait_until="domcontentloaded", timeout=nav_timeout)
                if state.active_tab: state.active_tab.heartbeat("navigation_end")
            else:
                # Fresh start
                if "gemini.google.com/app" not in page.url:
                    if state.active_tab: state.active_tab.heartbeat("navigation_start")
                    await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=nav_timeout)
                    if state.active_tab: state.active_tab.heartbeat("navigation_end")
            
            input_locator = page.locator(SELECTORS["INPUT"]).first
            if state.active_tab: state.active_tab.heartbeat("input_wait")
            await input_locator.wait_for(state="visible", timeout=15000)
            
            # 3. Auth Validation
            if state.active_tab: state.active_tab.heartbeat("auth_check")
            if not await engine.is_authenticated(page):
                if os.path.exists(session.state_path):
                    try: os.remove(session.state_path)
                    except: pass
                await session.ensure_healthy()
                raise HTTPException(status_code=401, detail="Authentication expired.")

            # 4. Observer Injection
            if state.active_tab: state.active_tab.heartbeat("observer_injection")
            observer_task = asyncio.create_task(
                page.evaluate(f"({STREAM_EXTRACTOR_SCRIPT})('{callback_name}')"),
                name=f"observer_{state.request_id}"
            )

            # Sync: Wait for JS Ready
            try:
                async with asyncio.timeout(5.0):
                    await state.js_ready.wait()
            except asyncio.TimeoutError:
                logger.warning("JS Ready Signal Timeout")

            # 5. Hardened Prompt Submission
            prompt = request.messages[-1].get("content", "")
            if state.active_tab: state.active_tab.heartbeat("prompt_fill")
            await input_locator.click()
            await input_locator.focus()
            await input_locator.fill(prompt)
            await page.keyboard.press("End")
            await asyncio.sleep(0.1)
            
            submit_button = page.get_by_role("button", name=re.compile("Send", re.I)).first
            if await submit_button.count() == 0:
                submit_button = page.locator(SELECTORS["SEND_BUTTON"]).first

            await submit_button.wait_for(state="visible", timeout=5000)
            
            # Conditional input stimulation if button is still disabled
            if not await submit_button.is_enabled():
                await page.keyboard.press("Space")
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.1)
            
            # Submission Loop (Retry fallback Enter)
            confirmed = False

            for attempt in range(2):
                # Every retry attempt must begin with a clean Event state 
                # for reliable edge-triggered confirmation.
                state.submission_confirmed.clear()

                if await submit_button.is_enabled():
                    await submit_button.click()
                else:
                    await page.keyboard.press("Enter")
                
                # Wait for authoritative confirmation from observer (Event-driven).
                # Only the first transition matters; duplicate events are safely ignored.
                try:
                    async with asyncio.timeout(3.5):
                        await state.submission_confirmed.wait()
                        confirmed = True
                        break
                except asyncio.TimeoutError:
                    if attempt == 0:
                        logger.warning("Submission not confirmed via events, retrying with Enter...", extra={"request_id": state.request_id})
                        continue

            if not confirmed:
                raise HTTPException(status_code=500, detail="Gemini failed to accept the prompt.")
            
            logger.info("Prompt submitted", extra={"request_id": state.request_id})

            is_stream = request.stream if request.stream is not None else False
            if is_stream:
                return StreamingResponse(
                    self._sse_generator(queue, request.model or "playwright/gemini", page, state, observer_task, engine, session, page_lease),
                    media_type="text/event-stream"
                )
            else:
                return await self._get_buffered_response(queue, request.model or "playwright/gemini", page, state, observer_task, engine, session, page_lease)

        except Exception as e:
            state.cleanup_started = True
            logger.error(f"Error in chat_completions: {e}", extra={"request_id": state.request_id})
            await self._cleanup(observer_task, state, page_lease, session)
            if isinstance(e, HTTPException): raise
            raise HTTPException(status_code=500, detail=str(e))

    async def _sse_generator(self, queue: asyncio.Queue, model: str, page: Page, state: RequestState, observer_task: Optional[asyncio.Task], engine: Any, session: Any, lease: ManagedPage):
        """Streaming generator with lazy conversation registration."""
        from app.utils.streaming import format_sse_chunk, get_done_chunk
        chunk_timeout = CONFIG["Playwright"].getint("chunk_timeout", 90)
        first_token_time = None
        
        try:
            while True:
                try:
                    # Lazy ID Extraction (Stateless -> Stateful Promotion)
                    # FIX: Atomic promotion using double-checked locking
                    if not state.conversation_id and not state.active_tab:
                        async with state.lock:
                            if not state.conversation_id and not state.active_tab:
                                match = re.search(r"/app/([a-z0-9]+)", page.url)
                                if match:
                                    state.conversation_id = match.group(1)
                                    # register_conversation links the lease to a new PersistentTab
                                    state.active_tab = await session.register_conversation(state.conversation_id, lease)
                                    # Heartbeat immediately after promotion to protect the new orphan
                                    state.active_tab.heartbeat("cleanup_id_found")

                    payload = await asyncio.wait_for(queue.get(), timeout=chunk_timeout)
                    if payload.get("type") == "done": break
                    
                    # Heartbeat update to prevent orphan cleanup
                    if state.active_tab:
                        state.active_tab.heartbeat("streaming_progress")

                    text_to_send = ""
                    if payload.get("type") == "chunk":
                        text_to_send = payload["delta"]
                    elif payload.get("type") == "rewrite" and not state.has_sent_text:
                        text_to_send = payload["full_text"]

                    if text_to_send:
                        if not first_token_time:
                            first_token_time = time.monotonic()
                            logger.info("Stream started", extra={"ttft": f"{first_token_time - state.start_time:.2f}s"})
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
        finally:
            await self._cleanup(observer_task, state, lease, session)

    async def _get_buffered_response(self, queue: asyncio.Queue, model: str, page: Page, state: RequestState, observer_task: Optional[asyncio.Task], engine: Any, session: Any, lease: ManagedPage):
        """Full response buffer."""
        total_timeout = CONFIG["Playwright"].getint("total_request_timeout", 120)
        try:
            full_text = ""
            async with asyncio.timeout(total_timeout):
                while True:
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
                    
                    # Heartbeat update to prevent orphan cleanup
                    if state.active_tab:
                        state.active_tab.heartbeat("buffering_progress")

                    if payload.get("type") == "rewrite": full_text = payload["full_text"]
                    if payload.get("type") == "chunk": full_text += payload["delta"]
            return self._convert_to_openai_format(full_text, model, stream=False, state=state)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Request timed out.")
        finally:
            await self._cleanup(observer_task, state, lease, session)

    async def _cleanup(self, observer_task: Optional[asyncio.Task], state: RequestState, lease: Optional[ManagedPage], session: Any):
        """Deteriminstic release of request resources."""
        async with state.lock:
            if state.cleanup_started and state.page_closed: return
            state.cleanup_started = True
            
            try:
                if observer_task and not observer_task.done():
                    observer_task.cancel()
                    try: await observer_task
                    except: pass
                
                # Final URL probe to salvage conversation ID
                if not state.active_tab and lease:
                    async with state.lock:
                        if not state.active_tab:
                            for _ in range(10): 
                                match = re.search(r"/app/([a-z0-9]+)", lease.page.url)
                                if match:
                                    state.conversation_id = match.group(1)
                                    state.active_tab = await session.register_conversation(state.conversation_id, lease)
                                    # Heartbeat immediately after promotion to protect the new orphan
                                    state.active_tab.heartbeat("cleanup_id_found")
                                    break
                                await asyncio.sleep(0.5)

                if lease:
                    # ManagedPage.close() handles BOTH:
                    # 1. Semaphore release (Always)
                    # 2. Page closing (Only if not persistent)
                    await lease.close()
                    state.page_closed = True
                    if state.active_tab:
                        logger.info(f"Lease returned for CID: {state.conversation_id}")
            except Exception as e:
                logger.warning(f"Cleanup Error: {e}")

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
