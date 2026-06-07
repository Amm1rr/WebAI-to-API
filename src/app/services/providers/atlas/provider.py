import time
from typing import Any, List
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
        Attempts dynamic discovery with a resilient fallback.
        """
        # 1. Check configuration
        try:
            atlas_client = get_atlas_client()
        except AtlasClientNotConfiguredError:
            # If not configured, we return an empty list to avoid advertising non-actionable models.
            return []

        # 2. Check Cache
        now = time.time()
        if self._model_cache and (now - self._cache_timestamp) < self._CACHE_TTL:
            return self._model_cache

        # 3. Dynamic Discovery
        fallback_id = "atlas/MiniMaxAI/MiniMax-M2"
        ts = int(now)
        
        try:
            raw_models = await atlas_client.list_models()
            normalized_models = []
            
            for m in raw_models:
                original_id = m.get("id")
                if not original_id:
                    continue
                
                # Normalize ID: prefix with atlas/ if not already present
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
                return normalized_models
            
            logger.warning("Atlas discovery returned empty list, using fallback.")

        except Exception as e:
            logger.warning("Atlas dynamic model discovery failed: %s. Using fallback.", e)

        # 4. Fallback (Cached briefly to avoid repeated failures while allowing recovery)
        fallback_models = [
            {
                "id": fallback_id,
                "object": "model",
                "created": ts,
                "owned_by": "atlascloud",
            }
        ]
        self._model_cache = fallback_models
        # Set timestamp such that it expires in _FALLBACK_CACHE_TTL
        self._cache_timestamp = now - (self._CACHE_TTL - self._FALLBACK_CACHE_TTL)
        return fallback_models

    async def close(self) -> None:
        # Atlas client handles its own httpx client lifecycle per request currently.
        # This could be optimized later, but for now we follow existing logic.
        pass
