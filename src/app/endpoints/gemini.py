# src/app/endpoints/gemini.py
import asyncio
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.logger import logger
from app.schemas.request import GeminiRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import get_gemini_chat_registry, generate_opaque_token

from pathlib import Path
from typing import Union, List, Optional

router = APIRouter()

@router.post(
    "/gemini",
    tags=["Legacy"],
    deprecated=True,
    summary="Legacy Stateless Gemini",
    description="Legacy stateless Gemini endpoint retained for backward compatibility. New integrations should prefer the OpenAI-compatible `/v1/chat/completions` endpoint."
)
async def gemini_generate(request: GeminiRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        files: Optional[List[Union[str, Path]]] = [Path(f) for f in request.files] if request.files else None
        
        if request.stream:
            async def sse_generator():
                try:
                    async for chunk in await gemini_client.generate_content_stream(request.message, request.model, files=files, gem=request.gem):
                        if chunk.text_delta:
                            yield f"data: {json.dumps({'response': chunk.text_delta}, ensure_ascii=False)}\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    # Client disconnected or generator closed, propagate to stop upstream
                    raise
                except Exception as e:
                    logger.error(f"Error in /gemini progressive streaming: {e}", exc_info=True)
                else:
                    # Explicit completion marker for successful streams
                    yield "data: [DONE]\n\n"

            return StreamingResponse(
                sse_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
            )

        # Non-streaming path
        response = await gemini_client.generate_content(request.message, request.model, files=files, gem=request.gem)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /gemini endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error generating content: {str(e)}")

@router.post(
    "/gemini-chat",
    tags=["Legacy"],
    deprecated=True,
    summary="Legacy In-Memory Conversation",
    description="Legacy conversation-oriented Gemini endpoint. Conversation state is maintained in memory only and does not survive server restarts. For persistent conversations, use `/v1/chat/completions` with `conversation_id`."
)
async def gemini_chat(request: GeminiRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    registry = get_gemini_chat_registry()
    if not registry:
        raise HTTPException(status_code=503, detail="Session registry is not initialized.")

    # 1. Resolve or generate conversation_id
    cid = request.conversation_id
    if cid:
        # Safeguard: Validate ID length
        if len(cid) > 64:
            raise HTTPException(status_code=400, detail="Invalid conversation_id length.")
    else:
        cid = generate_opaque_token()

    session_manager = await registry.get_session(cid)
    
    try:
        files: Optional[List[Union[str, Path]]] = [Path(f) for f in request.files] if request.files else None
        
        # 2. Progressive Streaming Path
        if request.stream:
            async def sse_generator():
                try:
                    async for payload in session_manager.get_streaming_response(request.model, request.message, files, request.gem):
                        # Add conversation_id to every chunk for consistency
                        payload["conversation_id"] = cid
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    # Final safety check: if we were cancelled but manager didn't send interrupt yet
                    # we don't try to yield here to avoid Starlette/FastAPI RuntimeErrors
                    raise
                except Exception as e:
                    logger.error(f"Error in /gemini-chat endpoint streaming: {e}", exc_info=True)
                else:
                    yield "data: [DONE]\n\n"

            return StreamingResponse(
                sse_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
            )

        # 3. Buffered Path
        response = await session_manager.get_response(request.model, request.message, files, request.gem)
        return {
            "response": response.text,
            "conversation_id": cid
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /gemini-chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error in chat: {str(e)}")
