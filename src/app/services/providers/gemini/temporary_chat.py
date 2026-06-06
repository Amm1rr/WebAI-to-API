import asyncio

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config import CONFIG
from app.logger import logger
from app.schemas.request import OpenAIChatRequest
from app.services.gemini_client import GeminiClientNotInitializedError, get_gemini_client
from app.services.multimodal import cleanup_staged_files, normalize_openai_chat_messages
from app.services.providers.gemini.shared import (
    build_tools_prompt,
    convert_to_openai_format,
    parse_tool_call,
    validate_model_name,
)
from app.services.providers.gemini.session_manager import transform_messages
from app.services.providers.gemini.webapi_response_builder import (
    build_webapi_chat_completion_response,
    build_webapi_streaming_artifact_chunk,
)
from app.utils.streaming import format_sse_chunk, get_done_chunk, simulate_streaming_generator


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


def _streaming_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def _prepare_temporary_chat_request(request: OpenAIChatRequest) -> dict[str, object]:
    model = _resolve_temporary_chat_model(request)
    normalized = normalize_openai_chat_messages(request.messages, allow_file_parts=True)
    tools_prompt = build_tools_prompt(request.tools) if request.tools else ""
    prompt = "\n\n".join(transform_messages(normalized.messages, tools_prompt))
    return {
        "model": model,
        "normalized": normalized,
        "prompt": prompt,
        "files": normalized.files or None,
        "is_stream": request.stream if request.stream is not None else False,
        "tools": request.tools,
        "gem": request.gem,
    }


def _build_cleanup_once(normalized):
    cleanup_started = False

    async def cleanup_once() -> None:
        nonlocal cleanup_started
        if cleanup_started:
            return
        cleanup_started = True
        await cleanup_staged_files(normalized)

    return cleanup_once


def _build_streaming_compatibility_response(openai_response: dict) -> StreamingResponse:
    # Tool requests currently use buffered SSE compatibility mode rather than fully
    # incremental tool-aware streaming, so the buffered response is replayed as SSE.
    return StreamingResponse(
        simulate_streaming_generator(openai_response),
        media_type="text/event-stream",
        headers=_streaming_headers(),
    )


async def _build_buffered_openai_response(
    gemini_client,
    *,
    prompt: str,
    model: str,
    files,
    gem,
    tools,
) -> dict:
    response = await gemini_client.generate_content(
        prompt,
        model,
        files=files,
        gem=gem,
        temporary=True,
    )
    response_text = getattr(response, "text", "") or ""
    tool_call = parse_tool_call(response_text) if tools else None
    return build_webapi_chat_completion_response(
        response,
        model,
        tool_call=tool_call,
    )


async def _build_incremental_streaming_response(
    gemini_client,
    *,
    prompt: str,
    model: str,
    files,
    gem,
    cleanup_once,
) -> StreamingResponse:
    async def sse_generator():
        final_response = None
        try:
            stream = await gemini_client.generate_content_stream(
                prompt,
                model,
                files=files,
                gem=gem,
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
        headers=_streaming_headers(),
    )


async def handle_temporary_chat_completions(request: OpenAIChatRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    prepared = _prepare_temporary_chat_request(request)
    cleanup_once = _build_cleanup_once(prepared["normalized"])

    try:
        if prepared["is_stream"] and not prepared["tools"]:
            return await _build_incremental_streaming_response(
                gemini_client,
                prompt=prepared["prompt"],
                model=prepared["model"],
                files=prepared["files"],
                gem=prepared["gem"],
                cleanup_once=cleanup_once,
            )

        openai_response = await _build_buffered_openai_response(
            gemini_client,
            prompt=prepared["prompt"],
            model=prepared["model"],
            files=prepared["files"],
            gem=prepared["gem"],
            tools=prepared["tools"],
        )
        if prepared["is_stream"]:
            return _build_streaming_compatibility_response(openai_response)
        return openai_response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in /v1/temporary/chat/completions endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error generating temporary content: {str(e)}")
    finally:
        await cleanup_once()
