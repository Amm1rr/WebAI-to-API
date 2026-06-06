# src/app/endpoints/chat.py
import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from app.config import CONFIG
from app.logger import logger
from app.openapi.chat_completions import (
    CHAT_COMPLETIONS_REQUEST_EXAMPLES,
    CHAT_COMPLETIONS_RESPONSE_200,
    TEMPORARY_CHAT_COMPLETIONS_REQUEST_EXAMPLES,
    TEMPORARY_CHAT_COMPLETIONS_RESPONSE_400,
)
from app.schemas.request import GeminiRequest, OpenAIChatRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.multimodal import cleanup_staged_files, normalize_openai_chat_messages
from app.services.providers.gemini.shared import (
    build_tools_prompt,
    convert_to_openai_format,
    parse_tool_call,
    validate_model_name,
)
from app.services.providers.gemini.session_manager import get_translate_session_manager
from app.services.providers.gemini.session_manager import transform_messages
from app.services.providers.gemini.webapi_response_builder import (
    build_webapi_chat_completion_response,
    build_webapi_streaming_artifact_chunk,
)
from app.services.factory import ProviderFactory
from app.services.model_catalog import list_models as build_model_catalog
from app.utils.streaming import format_sse_chunk, get_done_chunk, simulate_streaming_generator

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
    description="Extension-specific translation endpoint retained for compatibility with Translate It!-style browser extensions. This endpoint uses a shared global in-memory session, sends Gemini WebAPI translation requests as temporary requests so they are not saved in Gemini history, has no `conversation_id` support, does not support streaming, and does not survive server restarts. The client is responsible for sending a translation-specific prompt. For isolated or persistent translation workflows, use `/v1/chat/completions`."
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
        response = await session_manager.get_response(
            request.model,
            request.message,
            request.files,
            request.gem,
            temporary=True,
        )
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /translate endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during translation: {str(e)}")


def _resolve_temporary_chat_model(request: OpenAIChatRequest) -> str:
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")

    if request.conversation_id is not None:
        raise HTTPException(
            status_code=400,
            detail="conversation_id is not supported on the temporary chat endpoint.",
        )

    provider = (request.provider or "").strip().lower()
    if provider and provider != "gemini":
        raise HTTPException(
            status_code=400,
            detail="Only the Gemini provider is supported on the temporary chat endpoint.",
        )

    model = request.model or CONFIG["Gemini"].get("default_model", "gemini-3-flash")
    model = model.strip()

    if model.startswith("playwright/"):
        raise HTTPException(
            status_code=400,
            detail="Playwright models are not supported on the temporary chat endpoint.",
        )

    if model.startswith("atlas/"):
        raise HTTPException(
            status_code=400,
            detail="Atlas models are not supported on the temporary chat endpoint.",
        )

    if model.startswith("gemini/"):
        model = model.split("/", 1)[1].strip()

    validate_model_name(model)
    return model


@router.post(
    "/v1/temporary/chat/completions",
    tags=["Chat"],
    summary="Temporary OpenAI-Compatible Chat Completions",
    description=(
        "Gemini WebAPI-only OpenAI-compatible chat completions endpoint. Requests are sent with temporary=True, "
        "so responses are not saved in Gemini history and do not write SQLite conversation snapshots. "
        "`conversation_id` is rejected. Playwright models/providers, Atlas models/providers, and any non-Gemini provider are rejected. "
        "The endpoint supports streaming and non-streaming responses. File content parts are supported only by "
        "Gemini WebAPI, are request-scoped, and generated artifact metadata follows the same response shape as "
        "`/v1/chat/completions`."
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
    model = _resolve_temporary_chat_model(request)

    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    normalized = normalize_openai_chat_messages(request.messages, allow_file_parts=True)
    cleanup_started = False

    async def cleanup_once() -> None:
        nonlocal cleanup_started
        if cleanup_started:
            return
        cleanup_started = True
        await cleanup_staged_files(normalized)

    tools_prompt = build_tools_prompt(request.tools) if request.tools else ""
    prompt = "\n\n".join(transform_messages(normalized.messages, tools_prompt))
    files = normalized.files or None
    is_stream = request.stream if request.stream is not None else False

    if is_stream and not request.tools:

        async def sse_generator():
            final_response = None
            try:
                stream = await gemini_client.generate_content_stream(
                    prompt,
                    model,
                    files=files,
                    gem=request.gem,
                    temporary=True,
                )
                async for chunk in stream:
                    final_response = chunk
                    text_delta = getattr(chunk, "text_delta", "")
                    if text_delta:
                        openai_chunk = convert_to_openai_format(text_delta, model, stream=True)
                        yield await format_sse_chunk(openai_chunk)

                if final_response is not None:
                    artifact_chunk = build_webapi_streaming_artifact_chunk(final_response, model)
                    if artifact_chunk is not None:
                        artifact_chunk.pop("conversation_id", None)
                        artifact_chunk.pop("reused_conversation", None)
                        yield await format_sse_chunk(artifact_chunk)
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as e:
                logger.error(
                    f"Error in /v1/temporary/chat/completions progressive streaming: {e}",
                    exc_info=True,
                )
                raise
            else:
                yield await get_done_chunk()
            finally:
                await cleanup_once()

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        response = await gemini_client.generate_content(
            prompt,
            model,
            files=files,
            gem=request.gem,
            temporary=True,
        )
        response_text = getattr(response, "text", "") or ""
        tool_call = parse_tool_call(response_text) if request.tools else None
        openai_response = build_webapi_chat_completion_response(
            response,
            model,
            tool_call=tool_call,
        )
        if is_stream:
            # Tool requests currently use buffered SSE compatibility mode rather than fully
            # incremental tool-aware streaming, so the buffered response is replayed as SSE.
            return StreamingResponse(
                simulate_streaming_generator(openai_response),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        return openai_response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /v1/temporary/chat/completions endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error generating temporary content: {str(e)}")
    finally:
        await cleanup_once()


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
    responses={200: CHAT_COMPLETIONS_RESPONSE_200},
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
