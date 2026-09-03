"""Canonical stateless Gemini WebAPI implementation.

This module is the primary abstraction for client-owned-history,
temporary=True execution. Both /v1/stateless/chat/completions (canonical)
and /v1/temporary/chat/completions (deprecated compatibility wrapper)
delegate here. No Playwright/Atlas support, no conversation_id,
no persistence, no SQLite snapshots.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from gemini_webapi.exceptions import (
    APIError,
    AuthError,
    GeminiError,
    ModelInvalidError,
    TemporarilyBlockedError,
    TimeoutError as GeminiTimeoutError,
    UsageLimitExceededError,
)

from app.config import CONFIG
from app.logger import logger
from app.schemas.request import OpenAIChatRequest
from app.services.gemini_client import (
    GeminiClientNotInitializedError,
    acquire_current_gemini_lease,
)
from app.services.providers.gemini.streaming_response import GeminiLeaseStreamingResponse
from app.services.multimodal import (
    NormalizedOpenAIChatMessages,
    cleanup_staged_files,
    normalize_openai_chat_messages,
)
from app.services.providers.gemini.shared import (
    build_tools_prompt,
    ensure_gemini_client_ready,
    parse_tool_call,
    ToolCallParseStatus,
    validate_tool_history,
    validate_direct_webapi_model_name,
    validate_model_name,
)

from app.services.openai_compatibility import validate_openai_request_compatibility
from app.services.providers.exceptions import GeminiProviderOutputError
from app.services.providers.gemini.webapi_adapter import GeminiWebAPIAdapter
from app.services.providers.gemini.session_manager import transform_messages
from app.services.providers.gemini.webapi_response_builder import (
    build_webapi_chat_completion_response,
    build_webapi_streaming_artifact_chunk,
    build_progressive_terminal_chunk,
    build_progressive_text_chunk,
    create_stream_metadata,
)
from app.utils.streaming import (
    convert_chat_completion_to_streaming_chunk,
    format_sse_chunk,
    get_done_chunk,
    simulate_streaming_generator,
)


DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS = 300


@dataclass(slots=True)
class StatelessChatRequestContext:
    model: str
    normalized: NormalizedOpenAIChatMessages
    prompt: str
    files: list[Path] | None
    is_stream: bool
    tools: list[dict[str, Any]] | None
    gem: str | None


def _validate_stateless_chat_request(
    request: OpenAIChatRequest,
    *,
    endpoint_name: str = "stateless",
    direct_webapi_only: bool = True,
) -> str:
    endpoint_label = f"{endpoint_name} chat endpoint"
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")

    if request.conversation_id is not None:
        raise HTTPException(
            status_code=400,
            detail=f"conversation_id is not supported on the {endpoint_label}.",
        )

    provider = (request.provider or "").strip().lower()
    if provider and provider != "gemini":
        raise HTTPException(
            status_code=400,
            detail=f"Only the Gemini provider is supported on the {endpoint_label}.",
        )

    if request.provider_options and request.provider_options.gemini is not None:
        raise HTTPException(
            status_code=400,
            detail="provider_options.gemini is not supported by the Gemini WebAPI backend.",
        )

    if request.model is None:
        model = CONFIG["Gemini"].get("default_model", "gemini-3-flash")
    else:
        model = request.model.strip()
        if not model:
            raise HTTPException(
                status_code=400,
                detail=f"A non-empty model is required on the {endpoint_label}.",
            )

    # Case-insensitive check for routing namespaces only.
    lowered = model.lower()
    if lowered.startswith("playwright/"):
        raise HTTPException(
            status_code=400,
            detail=f"Playwright models are not supported on the {endpoint_label}.",
        )

    if lowered.startswith("atlas/"):
        raise HTTPException(
            status_code=400,
            detail=f"Atlas models are not supported on the {endpoint_label}.",
        )

    # Slash-containing model IDs are valid when the runtime Gemini catalog
    # reports them as available. Do not reject "/" generically; let the
    # resolver be the authority. Only playwright/atlas prefixes are rejected.

    if not direct_webapi_only and model.startswith("gemini/"):
        original_model = model
        model = model.split("/", 1)[1].strip()
        if not model:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{original_model}' must include a model name.",
            )

    return model


def _resolve_stateless_chat_model(
    request: OpenAIChatRequest,
    gemini_client,
    *,
    endpoint_name: str = "stateless",
    direct_webapi_only: bool = True,
) -> str:
    model = _validate_stateless_chat_request(
        request,
        endpoint_name=endpoint_name,
        direct_webapi_only=direct_webapi_only,
    )
    if direct_webapi_only:
        validate_direct_webapi_model_name(model, gemini_client)
    else:
        validate_model_name(model, gemini_client)
    return model


def _ensure_direct_webapi_ready(gemini_client) -> None:
    try:
        ensure_gemini_client_ready(gemini_client)
    except HTTPException as error:
        raise HTTPException(
            status_code=503,
            detail=f"Gemini WebAPI client is not ready: {error.detail}",
        ) from error


def _translate_direct_gemini_error(error: Exception) -> HTTPException | None:
    if isinstance(error, GeminiProviderOutputError):
        return HTTPException(status_code=502, detail="Gemini WebAPI returned malformed tool output.")
    if isinstance(error, AuthError):
        return HTTPException(
            status_code=503,
            detail="Gemini WebAPI authentication is unavailable.",
        )
    if isinstance(error, (asyncio.TimeoutError, GeminiTimeoutError)):
        return HTTPException(
            status_code=504,
            detail="Gemini WebAPI request timed out.",
        )
    if isinstance(error, UsageLimitExceededError):
        return HTTPException(
            status_code=429,
            detail="Gemini WebAPI usage limit exceeded.",
        )
    if isinstance(error, TemporarilyBlockedError):
        return HTTPException(
            status_code=429,
            detail="Gemini WebAPI request is temporarily blocked.",
        )
    if isinstance(error, ModelInvalidError):
        return HTTPException(
            status_code=502,
            detail="Gemini WebAPI rejected the requested model.",
        )
    if isinstance(error, (APIError, GeminiError)):
        return HTTPException(
            status_code=502,
            detail="Gemini WebAPI provider request failed.",
        )
    return None


def _streaming_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def _prepare_stateless_chat_request(
    request: OpenAIChatRequest,
    gemini_client,
    *,
    endpoint_name: str = "stateless",
    direct_webapi_only: bool = True,
) -> StatelessChatRequestContext:
    model = _resolve_stateless_chat_model(
        request,
        gemini_client,
        endpoint_name=endpoint_name,
        direct_webapi_only=direct_webapi_only,
    )
    normalized = normalize_openai_chat_messages(request.messages, allow_file_parts=True)
    return StatelessChatRequestContext(
        model=model,
        normalized=normalized,
        prompt="",
        files=normalized.files or None,
        is_stream=request.stream if request.stream is not None else False,
        tools=request.tools,
        gem=request.gem,
    )


def _build_cleanup_once(
    normalized: NormalizedOpenAIChatMessages,
) -> Callable[[], Awaitable[None]]:
    cleanup_task: asyncio.Task[None] | None = None

    async def cleanup_once() -> None:
        nonlocal cleanup_task
        if cleanup_task is None:
            cleanup_task = asyncio.create_task(cleanup_staged_files(normalized))
        await asyncio.shield(cleanup_task)

    return cleanup_once


def _build_streaming_compatibility_response(openai_response: dict) -> StreamingResponse:
    streaming_chunk = convert_chat_completion_to_streaming_chunk(openai_response)
    return StreamingResponse(
        simulate_streaming_generator(streaming_chunk),
        media_type="text/event-stream",
        headers=_streaming_headers(),
    )


async def _build_buffered_openai_response(
    gemini_client,
    *,
    prompt: str,
    model: str,
    files: list[Path] | None,
    gem: str | None,
    tools: list[dict[str, Any]] | None,
) -> dict:
    async with asyncio.timeout(DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS):
        response = await gemini_client.generate_content(
            prompt,
            model,
            files=files,
            gem=gem,
            temporary=True,
        )
    response_text = getattr(response, "text", "") or ""
    tool_call = None
    if tools:
        parse_result = parse_tool_call(response_text, tools=tools)
        if parse_result.status is ToolCallParseStatus.INVALID_TOOL_CALL:
            raise GeminiProviderOutputError(parse_result.error or "Gemini returned malformed tool-call output.")
        if parse_result.status is ToolCallParseStatus.VALID_TOOL_CALL:
            tool_call = parse_result.tool_call
    return build_webapi_chat_completion_response(
        response,
        model,
        tool_call=tool_call,
    )


async def _build_incremental_streaming_response(
    lease,
    *,
    prompt: str,
    model: str,
    files: list[Path] | None,
    gem: str | None,
    cleanup_once: Callable[[], Awaitable[None]],
    endpoint_name: str = "stateless",
) -> StreamingResponse:
    async def sse_generator():
        final_response = None
        completion_id, created = create_stream_metadata()
        execution_deadline = (
            asyncio.get_running_loop().time() + DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS
        )
        try:
            async with asyncio.timeout_at(execution_deadline):
                gemini_client = lease.client
                stream = await gemini_client.generate_content_stream(
                    prompt,
                    model,
                    files=files,
                    gem=gem,
                    temporary=True,
                )

            while True:
                try:
                    async with asyncio.timeout_at(execution_deadline):
                        chunk = await anext(stream)
                except StopAsyncIteration:
                    break

                final_response = chunk
                text_delta = getattr(chunk, "text_delta", "")
                if text_delta:
                    openai_chunk = build_progressive_text_chunk(
                        text_delta,
                        model,
                        completion_id=completion_id,
                        created=created,
                    )
                    yield await format_sse_chunk(openai_chunk)

            artifact_chunk = build_webapi_streaming_artifact_chunk(
                final_response,
                model,
                completion_id=completion_id,
                created=created,
            )
            if artifact_chunk is not None:
                artifact_chunk.pop("conversation_id", None)
                artifact_chunk.pop("reused_conversation", None)
                yield await format_sse_chunk(artifact_chunk)
            else:
                yield await format_sse_chunk(
                    build_progressive_terminal_chunk(
                        model,
                        completion_id=completion_id,
                        created=created,
                    )
                )
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except asyncio.TimeoutError:
            logger.error(
                f"Gemini WebAPI /v1/{endpoint_name}/chat/completions progressive streaming timed out after "
                f"{DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS}s"
            )
            return
        except (APIError, AuthError, GeminiError) as e:
            logger.error(
                f"Gemini WebAPI /v1/{endpoint_name}/chat/completions progressive streaming terminal failure: {e}",
                exc_info=True,
            )
            return
        else:
            yield await get_done_chunk()
        finally:
            await cleanup_once()

    response = GeminiLeaseStreamingResponse(
        sse_generator(),
        lease=lease,
        cleanup=cleanup_once,
        media_type="text/event-stream",
        headers=_streaming_headers(),
    )
    lease.transfer()
    return response


async def handle_stateless_chat_completions(
    request: OpenAIChatRequest,
    *,
    endpoint_name: str = "stateless",
    direct_webapi_only: bool = True,
):
    cleanup_once = None
    validate_openai_request_compatibility(
        request,
        GeminiWebAPIAdapter.openai_compatibility,
    )
    _validate_stateless_chat_request(
        request,
        endpoint_name=endpoint_name,
        direct_webapi_only=direct_webapi_only,
    )
    try:
        preparation_lease = acquire_current_gemini_lease()
    except (GeminiClientNotInitializedError, RuntimeError) as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        async with preparation_lease:
            _ensure_direct_webapi_ready(preparation_lease.client)
            prepared = _prepare_stateless_chat_request(
                request,
                preparation_lease.client,
                endpoint_name=endpoint_name,
                direct_webapi_only=direct_webapi_only,
            )
            cleanup_once = _build_cleanup_once(prepared.normalized)
            validate_tool_history(prepared.normalized.messages)
            tools_prompt = build_tools_prompt(prepared.tools) if prepared.tools else ""
            prepared.prompt = "\n\n".join(
                transform_messages(prepared.normalized.messages, tools_prompt)
            )

            if prepared.is_stream and not prepared.tools:
                response = await _build_incremental_streaming_response(
                    preparation_lease,
                    prompt=prepared.prompt,
                    model=prepared.model,
                    files=prepared.files,
                    gem=prepared.gem,
                    cleanup_once=cleanup_once,
                    endpoint_name=endpoint_name,
                )
                return response

            openai_response = await _build_buffered_openai_response(
                preparation_lease.client,
                prompt=prepared.prompt,
                model=prepared.model,
                files=prepared.files,
                gem=prepared.gem,
                tools=prepared.tools,
            )
            if prepared.is_stream:
                return _build_streaming_compatibility_response(openai_response)
            return openai_response
    except HTTPException:
        raise
    except Exception as e:
        translated_error = _translate_direct_gemini_error(e)
        if translated_error is not None:
            raise translated_error from e
        logger.error(
            f"Error in /v1/{endpoint_name}/chat/completions endpoint: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Internal error generating {endpoint_name} content.",
        ) from e
    finally:
        if cleanup_once is not None and not preparation_lease.is_transferred:
            try:
                await cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(f"Error cleaning up Gemini temporary request: {error}")
