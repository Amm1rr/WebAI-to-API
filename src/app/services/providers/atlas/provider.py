import asyncio
import time
from typing import Any, List, Optional
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from app.services.base import BaseProvider
from .client import get_atlas_client, AtlasClientNotConfiguredError, AtlasClientError
from app.logger import logger
from app.schemas.request import OpenAIChatRequest
from app.services.multimodal import normalize_openai_chat_messages

class AtlasProvider(BaseProvider):
    """
    HTTP-native provider for Atlas Cloud.
    Stateless and leverages direct streaming.
    """
    
    def __init__(self):
        self._model_cache: List[dict] = []
        self._cache_timestamp: float = 0
        self._CACHE_TTL: int = 3600  # 1 hour for success
        self._FALLBACK_CACHE_TTL: int = 300  # 5 minutes for failure
        self._current_ttl: int = 3600
        self._refresh_task: Optional[asyncio.Task] = None

    async def chat_completions(self, request: OpenAIChatRequest) -> Any:
        # Atlas logic currently splitting model name occurs in Factory, 
        # so we just need to ensure the request is mapped correctly.
        try:
            normalized = normalize_openai_chat_messages(
                request.messages,
                allow_file_parts=False,
            )
            request.messages = normalized.messages

            atlas_client = get_atlas_client()
            is_stream = request.stream if request.stream is not None else False
            
            response = await atlas_client.chat_completions(
                messages=request.messages,
                model=request.model, # This will be the resolved model name
                stream=is_stream,
                tools=request.tools,
                tool_choice=request.tool_choice,
            )

            if is_stream:
                async def stream_response():
                    try:
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk
                    finally:
                        await response.aclose()
                        await response._atlas_client.aclose()  # type: ignore[attr-defined]

                return StreamingResponse(
                    stream_response(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )

            data = response.json()
            await response.aclose()
            await response._atlas_client.aclose()  # type: ignore[attr-defined]
            return data

        except AtlasClientNotConfiguredError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except AtlasClientError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error in AtlasProvider.chat_completions: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Error processing Atlas chat completion: {str(e)}",
            )

    async def list_models(self) -> List[dict]:
        """
        Return a list of supported models for Atlas Cloud.
        Returns cached or fallback models immediately and refreshes in background.
        """
        # 1. Check configuration
        try:
            get_atlas_client()
        except AtlasClientNotConfiguredError:
            return []

        # 2. Trigger background refresh if needed
        now = time.time()
        if not self._model_cache or (now - self._cache_timestamp) >= self._current_ttl:
            self._trigger_refresh()

        # 3. Return cache or immediate fallback
        if self._model_cache:
            return self._model_cache

        return self._get_fallback_models()

    def _trigger_refresh(self) -> None:
        """Schedule a background refresh if one isn't already running."""
        if self._refresh_task and not self._refresh_task.done():
            return
        
        try:
            loop = asyncio.get_running_loop()
            self._refresh_task = loop.create_task(self._refresh_models())
        except RuntimeError:
            # Fallback for environments without a running loop
            logger.warning("Failed to create Atlas refresh task: no running event loop.")

    async def _refresh_models(self) -> None:
        """Perform the actual network request to Atlas to update the model cache."""
        try:
            atlas_client = get_atlas_client()
            raw_models = await atlas_client.list_models()
            now = time.time()
            ts = int(now)
            
            normalized_models = []
            for m in raw_models:
                original_id = m.get("id")
                if not original_id:
                    continue
                
                model_id = original_id if original_id.startswith("atlas/") else f"atlas/{original_id}"
                normalized_models.append({
                    "id": model_id,
                    "object": "model",
                    "created": m.get("created", ts),
                    "owned_by": "atlascloud",
                })
            
            if normalized_models:
                self._model_cache = normalized_models
                self._cache_timestamp = now
                self._current_ttl = self._CACHE_TTL
                logger.debug("Atlas model cache updated with %d models.", len(normalized_models))
                return

            logger.warning("Atlas discovery returned empty list.")

        except AtlasClientNotConfiguredError:
            # Configuration was lost while task was pending
            self._model_cache = []
            return
        except Exception as e:
            logger.warning("Atlas dynamic model discovery failed: %s", e)

        # On failure or empty list, ensure we don't block next time but retry in FALLBACK_CACHE_TTL
        now = time.time()
        if not self._model_cache:
            self._model_cache = self._get_fallback_models()
            
        self._cache_timestamp = now
        self._current_ttl = self._FALLBACK_CACHE_TTL

    def _get_fallback_models(self) -> List[dict]:
        """Return the hardcoded safety fallback model list."""
        return [
            {
                "id": "atlas/MiniMaxAI/MiniMax-M2",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "atlascloud",
            }
        ]

    async def close(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
