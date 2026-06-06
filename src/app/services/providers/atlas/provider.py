import os
import httpx
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
            logger.error(f"Error in AtlasProvider.chat_completions: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Error processing Atlas chat completion: {str(e)}",
            )

    async def list_models(self) -> List[dict]:
        import time
        ts = int(time.time())
        return [
            {
                "id": "atlas/MiniMaxAI/MiniMax-M2",
                "object": "model",
                "created": ts,
                "owned_by": "atlascloud",
            }
        ]

    async def close(self) -> None:
        # Atlas client handles its own httpx client lifecycle per request currently.
        # This could be optimized later, but for now we follow existing logic.
        pass
