# src/app/endpoints/gemini.py
from fastapi import APIRouter, HTTPException
from app.logger import logger
from schemas.request import GeminiRequest
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
        # Use the value attribute for the model (since GeminiRequest.model is an Enum)
        files: Optional[List[Union[str, Path]]] = [Path(f) for f in request.files] if request.files else None
        response = await gemini_client.generate_content(request.message, request.model.value, files=files)
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
        response = await session_manager.get_response(request.model, request.message, request.files)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /gemini-chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error in chat: {str(e)}")
