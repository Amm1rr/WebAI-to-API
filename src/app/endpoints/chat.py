# src/app/endpoints/chat.py
import json
import time
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.logger import logger
from app.schemas.request import GeminiRequest, OpenAIChatRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import get_translate_session_manager
from app.services.factory import ProviderFactory

router = APIRouter()


@router.get("/v1/gems")
async def list_gems():
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        gems = await gemini_client.fetch_gems()
        return {
            "gems": [
                {
                    "id": gem.id,
                    "name": gem.name,
                    "description": gem.description,
                    "predefined": gem.predefined,
                }
                for gem in gems
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching gems: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching gems: {str(e)}")


@router.post("/translate")
async def translate_chat(request: GeminiRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    session_manager = get_translate_session_manager()
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager is not initialized.")
    try:
        response = await session_manager.get_response(request.model, request.message, request.files, request.gem)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /translate endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during translation: {str(e)}")


@router.get("/v1/models")
async def list_models():
    all_models = []
    # Collect models from all registered providers in the registry
    for provider_key in ProviderFactory._registry.keys():
        # Using a dummy request to resolve the provider instance via factory
        dummy_request = OpenAIChatRequest(messages=[], provider=provider_key)
        provider, _ = ProviderFactory.get_provider(dummy_request)
        all_models.extend(await provider.list_models())
    
    return {
        "object": "list",
        "data": all_models
    }


@router.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest):
    # Resolve provider and model name via the static factory
    provider, resolved_model = ProviderFactory.get_provider(request)
    
    # Update the request with the resolved model name so the provider gets the clean version
    request.model = resolved_model
    
    # Delegate implementation-heavy work to the provider
    return await provider.chat_completions(request)
