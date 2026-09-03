# src/app/endpoints/chat.py
from fastapi import APIRouter, HTTPException, Request
from app.logger import logger
from app.openapi.chat_completions import (
    CHAT_COMPLETIONS_REQUEST_EXAMPLES,
    CHAT_COMPLETIONS_RESPONSE_400,
    CHAT_COMPLETIONS_RESPONSE_200,
    STATELESS_CHAT_COMPLETIONS_ERROR_RESPONSES,
    STATELESS_CHAT_COMPLETIONS_REQUEST_EXAMPLES,
    STATELESS_CHAT_COMPLETIONS_RESPONSE_400,
    TEMPORARY_CHAT_COMPLETIONS_REQUEST_EXAMPLES,
    TEMPORARY_CHAT_COMPLETIONS_RESPONSE_400,
)
from app.schemas.request import GeminiRequest, OpenAIChatRequest
from app.services.gemini_client import (
    acquire_current_gemini_lease,
    GeminiClientNotInitializedError,
)
from app.services.factory import ProviderFactory
from app.services.model_catalog import list_models as build_model_catalog
from app.services.model_catalog import list_stateless_models as build_stateless_model_catalog
from app.services.openai_compatibility import validate_openai_request_compatibility
from app.services.providers.gemini.stateless_chat import handle_stateless_chat_completions
from app.services.providers.gemini.temporary_chat import (
    handle_temporary_chat_completions,
)
from app.services.providers.gemini.shared import (
    ensure_gemini_client_ready,
    validate_direct_webapi_model_name,
)

router = APIRouter()


@router.get(
    "/v1/gems",
    tags=["Utilities"],
    summary="List Available Gems",
    description="Returns available Gemini Gems associated with the account. Can be used to apply specific personas in chat requests."
)
async def list_gems():
    try:
        lease = acquire_current_gemini_lease()
    except (GeminiClientNotInitializedError, RuntimeError) as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        async with lease:
            gems = await lease.client.fetch_gems()
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
    description="Extension-specific translation endpoint retained for compatibility with Translate It!-style browser extensions. This endpoint executes stateless Gemini WebAPI requests concurrently, sends them as temporary requests so they are not saved in Gemini history, has no `conversation_id` support, does not support streaming, and does not maintain conversation state. The client is responsible for sending a translation-specific prompt. For persistent translation workflows, use `/v1/chat/completions`."
)
async def translate_chat(request: GeminiRequest):
    try:
        lease = acquire_current_gemini_lease()
    except (GeminiClientNotInitializedError, RuntimeError) as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        async with lease:
            gemini_client = lease.client
            ensure_gemini_client_ready(gemini_client)
            validate_direct_webapi_model_name(request.model, gemini_client)
            response = await gemini_client.generate_content(
                request.message,
                request.model,
                files=request.files,
                gem=request.gem,
                temporary=True,
            )
            return {"response": response.text}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /translate endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during translation: {str(e)}")
@router.post(
    "/v1/temporary/chat/completions",
    tags=["Chat"],
    summary="Temporary OpenAI-Compatible Chat Completions (Deprecated)",
    deprecated=True,
    description=(
        "Deprecated compatibility endpoint. New integrations must use the canonical "
        "`/v1/stateless/chat/completions` endpoint. This endpoint remains for backward "
        "compatibility and delegates to the same stateless Gemini WebAPI implementation "
        "(temporary=True, so responses are not saved in Gemini history and do not write "
        "SQLite conversation snapshots; client-owned history, `conversation_id` is rejected). "
        "Gemini WebAPI-only; Playwright, Atlas, and non-Gemini providers are rejected. "
        "Malformed audited OpenAI controls return HTTP 422; controls unsupported by Gemini "
        "WebAPI return HTTP 400. streaming and non-streaming responses, file parts, and "
        "generated artifact metadata follow the same response shape as `/v1/chat/completions`."
    ),
    responses={
        200: CHAT_COMPLETIONS_RESPONSE_200,
        400: TEMPORARY_CHAT_COMPLETIONS_RESPONSE_400,
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": TEMPORARY_CHAT_COMPLETIONS_REQUEST_EXAMPLES,
                }
            }
        }
    },
)
async def temporary_chat_completions(request: OpenAIChatRequest):
    return await handle_temporary_chat_completions(request)


@router.post(
    "/v1/stateless/chat/completions",
    tags=["Chat"],
    summary="Stateless OpenAI-Compatible Chat Completions",
    description=(
        "Canonical stateless Gemini WebAPI endpoint. Client-owned-history execution: every request is "
        "self-contained, uses temporary=True, rejects `conversation_id`, does not create SQLite conversation "
        "snapshots, and does not persist Gemini conversation history. Gemini WebAPI only; Playwright, Atlas, "
        "and other non-Gemini providers are not supported. Slash-containing model IDs are valid when advertised "
        "by `/v1/stateless/models` and recognized as available by the Gemini WebAPI runtime catalog; unknown "
        "slash IDs and non-Gemini routing IDs are rejected. Malformed request values return HTTP 422; unsupported "
        "Gemini controls, providers, backends, and `provider_options.gemini` return HTTP 400. Buffered responses, "
        "progressive SSE streaming, multimodal file parts, and one generated function tool call are supported. "
        "`stream=true` with tools uses buffered OpenAI-compatible SSE replay rather than native progressive tool "
        "streaming. `max_tokens`, `max_completion_tokens`, `reasoning_effort`, and `stream_options.include_usage` "
        "are accepted compatibility no-ops. Direct Gemini WebAPI execution has a 300-second deadline."
    ),
    responses={
        200: CHAT_COMPLETIONS_RESPONSE_200,
        400: STATELESS_CHAT_COMPLETIONS_RESPONSE_400,
        **STATELESS_CHAT_COMPLETIONS_ERROR_RESPONSES,
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": STATELESS_CHAT_COMPLETIONS_REQUEST_EXAMPLES,
                }
            }
        }
    },
)
async def stateless_chat_completions(request: OpenAIChatRequest):
    return await handle_stateless_chat_completions(
        request,
        endpoint_name="stateless",
        direct_webapi_only=True,
    )


@router.get(
    "/v1/stateless/models",
    tags=["Chat"],
    summary="List Stateless Models",
    description=(
        "Returns only currently available direct Gemini WebAPI models that satisfy the stateless execution contract, "
        "including valid slash-containing model IDs when advertised by the Gemini WebAPI runtime catalog. "
        "Playwright stateless execution is not implemented; Playwright models, legacy browser aliases, Atlas models, "
        "and other provider models are not included."
    ),
)
async def get_stateless_models():
    return await build_stateless_model_catalog()


@router.get(
    "/v1/models",
    tags=["Chat"],
    summary="List Available Models",
    description="Returns available models from all registered providers. Includes provider-prefixed models used for discovery and routing."
)
async def get_models():
    return await build_model_catalog(include_legacy_playwright_aliases=False, allow_stale=False)


@router.post(
    "/v1/chat/completions",
    tags=["Chat"],
    summary="OpenAI-Compatible Chat Completions",
    description=(
        "Primary OpenAI-compatible chat completions endpoint. Gemini WebAPI supports file content parts; file parts are request-scoped and unsupported backends reject them. "
        "For Gemini WebAPI, text parts are concatenated into one prompt and file parts are passed as attachments, so exact text/file interleaving is not preserved. "
        "Supported file formats are documented in docs/api.md. Audited OpenAI request controls are schema-validated, and explicitly unsupported controls return HTTP 400. "
        "This is the recommended API for new integrations."
    ),
    responses={200: CHAT_COMPLETIONS_RESPONSE_200, 400: CHAT_COMPLETIONS_RESPONSE_400},
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": CHAT_COMPLETIONS_REQUEST_EXAMPLES,
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

    validate_openai_request_compatibility(
        request,
        provider.get_openai_compatibility_capabilities(request),
    )

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
