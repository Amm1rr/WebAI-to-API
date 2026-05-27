import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from app.services.base import BaseProvider
from app.services.browser.engine import get_browser_engine
from app.schemas.request import OpenAIChatRequest
from app.logger import logger
from app.config import CONFIG
from .gemini_playwright_scripts import STREAM_EXTRACTOR_SCRIPT, SELECTORS

@dataclass
class RequestState:
    """Immutable-like shared state for a single request lifecycle."""
    request_id: str
    start_time: float
    permit_acquired: bool = False
    semaphore_released: bool = False
    cleanup_started: bool = False
    page_closed: bool = False
    dropped_chunks: int = 0
    max_queue_depth: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

class GeminiPlaywrightProvider(BaseProvider):
    """
    Production-grade Browser-native provider for Gemini Web.
    Features: 
    - Generation-based context rotation (self-healing without active stream drops)
    - Strict semaphore ownership via RequestState
    - Non-blocking browser bridge with backpressure metrics
    - Rewrite-resilient DOM diffing
    - Structured metrics & logging
    """

    async def chat_completions(self, request: OpenAIChatRequest) -> Any:
        engine = await get_browser_engine()
        state = RequestState(request_id=str(uuid.uuid4()).replace("-", "_"), start_time=time.time())
        
        # 1. Strict Semaphore Acquisition
        await engine.semaphore.acquire()
        state.permit_acquired = True
        
        page = None
        observer_task = None

        try:
            # 2. Page Acquisition (Supports Self-Healing)
            page = await engine.get_page()
            
            callback_name = f"emit_{state.request_id}"
            # Bounded queue with non-blocking bridge
            queue = asyncio.Queue(maxsize=100)

            async def bridge_callback(source, payload):
                if state.cleanup_started: return
                
                # Non-blocking put to prevent stalling Playwright internals
                try:
                    queue.put_nowait(payload)
                    state.max_queue_depth = max(state.max_queue_depth, queue.qsize())
                except asyncio.QueueFull:
                    state.dropped_chunks += 1
                    logger.warning("Bridge Queue Full: Dropping chunk", extra={"request_id": state.request_id})

            await page.expose_binding(callback_name, bridge_callback)
            
            nav_timeout = CONFIG["Playwright"].getint("navigation_timeout", 30000)
            ui_timeout = CONFIG["Playwright"].getint("ui_wait_timeout", 15000)

            logger.info("Navigating to Gemini Web", extra={
                "request_id": state.request_id, 
                "active_pages": engine.active_pages,
                "context_gen": engine.context_generation
            })
            
            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=nav_timeout)
            
            # Verify UI interactivity
            try:
                await page.wait_for_selector(SELECTORS["INPUT"], state="attached", timeout=ui_timeout)
            except PlaywrightTimeoutError:
                if "accounts.google.com" in page.url:
                    raise HTTPException(status_code=401, detail="Authentication required. Run verify_login.py.")
                raise HTTPException(status_code=503, detail="Gemini UI failed to load interactive state.")

            # 3. Pre-submit Observer Injection
            observer_task = asyncio.create_task(
                page.evaluate(f"({STREAM_EXTRACTOR_SCRIPT})('{callback_name}')"),
                name=f"observer_{state.request_id}"
            )

            # 4. Resilient Submission
            input_locator = page.locator(SELECTORS["INPUT"]).first
            await input_locator.wait_for(state="visible", timeout=5000)
            
            prompt = request.messages[-1].get("content", "")
            await input_locator.fill(prompt)
            
            submit_button = page.locator(SELECTORS["SEND_BUTTON"]).first
            await submit_button.wait_for(state="visible", timeout=5000)
            await submit_button.click()
            
            logger.info("Prompt submitted", extra={
                "request_id": state.request_id, 
                "latency_to_submit": time.time() - state.start_time
            })

            is_stream = request.stream if request.stream is not None else False

            if is_stream:
                return StreamingResponse(
                    self._sse_generator(queue, request.model or "playwright/gemini", page, state, observer_task, engine),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    }
                )
            else:
                return await self._get_buffered_response(queue, request.model or "playwright/gemini", page, state, observer_task, engine)

        except Exception as e:
            state.cleanup_started = True
            logger.error(f"Error in chat_completions: {e}", extra={"request_id": state.request_id})
            await self._cleanup(page, observer_task, state, engine)
            if isinstance(e, HTTPException): raise
            raise HTTPException(status_code=500, detail=str(e))

    async def _sse_generator(self, queue: asyncio.Queue, model: str, page: Page, state: RequestState, observer_task: Optional[asyncio.Task], engine: Any):
        """Streaming generator with rewrite support and cancellation propagation."""
        from app.utils.streaming import format_sse_chunk, get_done_chunk
        chunk_timeout = CONFIG["Playwright"].getint("chunk_timeout", 90)
        first_token_time = None
        
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=chunk_timeout)
                    
                    if payload.get("type") == "done":
                        break
                    
                    if payload.get("type") == "rewrite":
                        # For now, we emit the full text as a new delta or skip. 
                        # In production, we'd emit a 'rewrite' event if clients supported it.
                        # Gemini often 'polishes' output, causing a rewrite event.
                        logger.info("Handling Gemini UI Rewrite", extra={"request_id": state.request_id})
                        continue

                    if payload.get("type") == "chunk":
                        if not first_token_time:
                            first_token_time = time.time()
                            logger.info("First token received", extra={
                                "request_id": state.request_id, 
                                "ttft": first_token_time - state.start_time
                            })
                        
                        chunk = self._convert_to_openai_format(payload["delta"], model, stream=True)
                        yield await format_sse_chunk(chunk)
                        
                except asyncio.TimeoutError:
                    logger.warning("Stream chunk timeout", extra={"request_id": state.request_id})
                    break
            
            yield await get_done_chunk()
            logger.info("Stream finished successfully", extra={
                "request_id": state.request_id, 
                "duration": time.time() - state.start_time,
                "max_q_depth": state.max_queue_depth,
                "dropped_chunks": state.dropped_chunks
            })
            
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("Client disconnected, stopping generation", extra={"request_id": state.request_id})
            try:
                stop_button = page.locator(SELECTORS["STOP_BUTTON"]).first
                if await stop_button.is_visible():
                    await stop_button.click()
            except: pass
            raise
        finally:
            await self._cleanup(page, observer_task, state, engine)

    async def _get_buffered_response(self, queue: asyncio.Queue, model: str, page: Page, state: RequestState, observer_task: Optional[asyncio.Task], engine: Any):
        """Buffered collector with total timeout budget."""
        total_timeout = CONFIG["Playwright"].getint("total_request_timeout", 120)
        try:
            full_text = ""
            async with asyncio.timeout(total_timeout):
                while True:
                    payload = await queue.get()
                    if payload.get("type") == "done":
                        break
                    if payload.get("type") == "rewrite":
                        full_text = payload["full_text"]
                    if payload.get("type") == "chunk":
                        full_text += payload["delta"]
            
            logger.info("Buffered response complete", extra={
                "request_id": state.request_id, 
                "duration": time.time() - state.start_time,
                "max_q_depth": state.max_queue_depth,
                "dropped_chunks": state.dropped_chunks
            })
            return self._convert_to_openai_format(full_text, model, stream=False)
        except asyncio.TimeoutError:
            logger.warning("Buffered request timed out", extra={"request_id": state.request_id})
            raise HTTPException(status_code=504, detail="Request timed out during generation.")
        finally:
            await self._cleanup(page, observer_task, state, engine)

    async def _cleanup(self, page: Optional[Page], observer_task: Optional[asyncio.Task], state: RequestState, engine: Any):
        """Standardized, idempotent cleanup."""
        async with state.lock:
            if state.cleanup_started and state.page_closed and state.semaphore_released:
                return # Already cleaned up
            
            state.cleanup_started = True
            
            try:
                # 1. Task Cancellation
                if observer_task and not observer_task.done():
                    observer_task.cancel()
                    try:
                        await observer_task
                    except (asyncio.CancelledError, PlaywrightError):
                        pass

                # 2. Page Closure
                if page and not state.page_closed:
                    try:
                        if not page.is_closed():
                            await page.close()
                    except PlaywrightError as e:
                        if "closed" not in str(e).lower() and "destroyed" not in str(e).lower():
                            logger.warning(f"Error closing page: {e}", extra={"request_id": state.request_id})
                    
                    await engine.notify_page_closed(page)
                    state.page_closed = True

            except Exception as e:
                logger.warning(f"Cleanup error: {e}", extra={"request_id": state.request_id})
            finally:
                # 3. STRICT Semaphore Ownership Release
                if state.permit_acquired and not state.semaphore_released:
                    engine.semaphore.release()
                    state.semaphore_released = True
                    logger.info("Resources released", extra={
                        "request_id": state.request_id,
                        "remaining_active": engine.active_pages
                    })

    async def list_models(self) -> List[dict]:
        return [
            {
                "id": "playwright/gemini",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "google-playwright",
            }
        ]

    async def close(self) -> None:
        pass

    def _convert_to_openai_format(self, text: str, model: str, stream: bool):
        ts = int(time.time())
        choice_key = "delta" if stream else "message"
        content = {"role": "assistant", "content": text}
        return {
            "id": f"chatcmpl-{ts}",
            "object": "chat.completion.chunk" if stream else "chat.completion",
            "created": ts,
            "model": model,
            "choices": [{
                "index": 0,
                choice_key: content,
                "finish_reason": "stop" if not stream else None,
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
