# src/app/endpoints/gemini.py
import asyncio
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.logger import logger
from app.schemas.request import GeminiRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import get_gemini_chat_manager

from pathlib import Path
from typing import Union, List, Optional

router = APIRouter()

@router.post("/gemini")
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
                            yield f"data: {json.dumps({'response': chunk.text_delta})}\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    # Client disconnected or generator closed, propagate to stop upstream
                    raise
                except Exception as e:
                    logger.error(f"Error in /gemini progressive streaming: {e}", exc_info=True)
                finally:
                    # Unlike OpenAI, we don't necessarily need [DONE] here as it's a custom endpoint,
                    # but we keep it simple and just close the connection.

                    # Note: We don't yield a final message here to stay minimal and clean, 
                    # standard EventSource will just close.
                    pass

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

@router.post("/gemini-chat")
async def gemini_chat(request: GeminiRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    session_manager = get_gemini_chat_manager()
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager is not initialized.")
    try:
        response = await session_manager.get_response(request.model, request.message, request.files, request.gem)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /gemini-chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error in chat: {str(e)}")
