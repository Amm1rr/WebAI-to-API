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


async def handle_temporary_chat_completions(request: OpenAIChatRequest):
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
