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
from app.services.model_catalog import list_models as build_model_catalog

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
async def get_models():
    return await build_model_catalog(include_legacy_playwright_aliases=False)


@router.post(
    "/v1/chat/completions",
    tags=["Chat"],
    summary="OpenAI-Compatible Chat Completions",
    description=(
        "Primary OpenAI-compatible chat completions endpoint. Gemini WebAPI supports file content parts; file parts are request-scoped and unsupported backends reject them. "
        "For Gemini WebAPI, text parts are concatenated into one prompt and file parts are passed as attachments, so exact text/file interleaving is not preserved. "
        "Supported file formats are documented in docs/api.md. This is the recommended API for new integrations."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "textOnly": {
                            "summary": "Text-only request",
                            "value": {
                                "model": "gemini-3-flash",
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": "Hello!"
                                    }
                                ]
                            },
                        },
                        "fileRequest": {
                            "summary": "File attachment request",
                            "value": {
                                "model": "gemini-3-flash",
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": [
                                            {"type": "text", "text": "Summarize this document."},
                                            {
                                                "type": "file",
                                                "file": {
                                                    "filename": "invoice.pdf",
                                                    "file_data": "data:application/pdf;base64,JVBERi0xLjQK",
                                                },
                                            },
                                        ],
                                    }
                                ],
                            },
                        },
                    }
                }
            }
        }
    },
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


@router.get(
    "/v1/conversations",
    tags=["Chat"],
    summary="List Gemini WebAPI Conversations",
    description="Lists locally persisted Gemini WebAPI conversations stored in SQLite. Playwright and Atlas conversations are not included."
)
async def list_conversations():
    provider, _ = ProviderFactory.get_provider(
        OpenAIChatRequest(messages=[], provider="gemini")
    )
    list_handler = getattr(provider, "list_conversations", None)
    if list_handler is None:
        raise HTTPException(status_code=400, detail="Conversation listing is not supported for this provider.")
    return await list_handler()


@router.delete(
    "/v1/conversations",
    tags=["Chat"],
    summary="Bulk Delete Gemini WebAPI Conversations",
    description="Deletes all locally persisted Gemini WebAPI conversations. Playwright and Atlas conversations are not supported."
)
async def delete_conversations():
    provider, _ = ProviderFactory.get_provider(
        OpenAIChatRequest(messages=[], provider="gemini")
    )
    delete_handler = getattr(provider, "delete_conversations", None)
    if delete_handler is None:
        raise HTTPException(status_code=400, detail="Bulk conversation deletion is not supported for this provider.")
    return await delete_handler()


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
