# src/app/endpoints/chat.py
import json
import time
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from app.logger import logger
from app.schemas.request import GeminiRequest, OpenAIChatRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.providers.gemini.session_manager import get_translate_session_manager
from app.services.factory import ProviderFactory

router = APIRouter()


@router.get(
    "/v1/gems",
    tags=["Utilities"],
    summary="List Available Gems",
    description="Returns available Gemini Gems associated with the account. Can be used to apply specific personas in chat requests."
)
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


@router.post(
    "/translate",
    tags=["Translation"],
    summary="Translate Extension Compatibility",
    description="Extension-specific translation endpoint retained for compatibility with Translate It!-style browser extensions. This endpoint uses a shared global in-memory session, does not support conversation_id isolation, does not support streaming, and does not survive server restarts. The client is responsible for sending a translation-specific prompt. For isolated or persistent translation workflows, use `/v1/chat/completions`."
)
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


@router.get(
    "/v1/models",
    tags=["Chat"],
    summary="List Available Models",
    description="Returns available models from all registered providers. Includes provider-prefixed models used for discovery and routing."
)
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


@router.post(
    "/v1/chat/completions",
    tags=["Chat"],
    summary="OpenAI-Compatible Chat Completions",
    description="Primary OpenAI-compatible chat endpoint. Supports streaming responses, conversation_id-based conversations, and provider routing. This is the recommended API for new integrations."
)
async def chat_completions(request: OpenAIChatRequest, http_request: Request):
    # Attach HTTP request_id for observability (will be used by adapter if present)
    # The middleware sets request.state.request_id
    if hasattr(http_request.state, "request_id"):
        # Attach to the Pydantic model as an extra attribute (not validated).
        # NOTE: This is for observability only and NOT part of the API contract.
        # Clients should NOT rely on this field.
        object.__setattr__(request, "_http_request_id", http_request.state.request_id)

    # Resolve provider and model name via the static factory
    provider, resolved_model = ProviderFactory.get_provider(request)

    # Update the request with the resolved model name so the provider gets the clean version
    request.model = resolved_model

    # Delegate implementation-heavy work to the provider
    return await provider.chat_completions(request)


@router.delete(
    "/v1/conversations/{conversation_id}",
    tags=["Chat"],
    summary="Delete Gemini WebAPI Conversation",
    description="Deletes a Gemini WebAPI conversation by local conversation_id. Playwright and Atlas conversations are not supported."
)
async def delete_conversation(conversation_id: str):
    provider, _ = ProviderFactory.get_provider(
        OpenAIChatRequest(messages=[], provider="gemini")
    )
    delete_handler = getattr(provider, "delete_conversation", None)
    if delete_handler is None:
        raise HTTPException(status_code=400, detail="Conversation deletion is not supported for this provider.")
    return await delete_handler(conversation_id)
