# src/app/endpoints/chat.py
import json
import time
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.logger import logger
from schemas.request import GeminiRequest, OpenAIChatRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError
from app.services.session_manager import get_translate_session_manager

router = APIRouter()

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
        # This call now correctly uses the fixed session manager
        response = await session_manager.get_response(request.model, request.message, request.files)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /translate endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during translation: {str(e)}")

def _make_chunk(chat_id: str, model: str, delta_content: str, finish_reason=None) -> str:
    """Format a single SSE data line in OpenAI chat.completion.chunk format."""
    chunk = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": delta_content} if delta_content else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"

def convert_to_openai_format(response_text: str, model: str):
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

def _build_prompt(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not content:
            continue
        if role == "system":
            parts.append(f"System: {content}")
        elif role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
    return "\n\n".join(parts)

@router.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")

    if not request.model:
        raise HTTPException(status_code=400, detail="Model not specified in the request.")

    final_prompt = _build_prompt(request.messages)
    if not final_prompt:
        raise HTTPException(status_code=400, detail="No valid messages found.")

    model_value = request.model.value
    is_stream = request.stream if request.stream is not None else False

    if is_stream:
        chat_id = f"chatcmpl-{int(time.time())}"

        async def event_generator():
            try:
                # Send role delta first (matches OpenAI behaviour)
                role_chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_value,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(role_chunk)}\n\n"

                async for chunk in gemini_client.generate_content_stream(
                    message=final_prompt, model=model_value, files=None
                ):
                    delta = chunk.text_delta
                    if delta:
                        yield _make_chunk(chat_id, model_value, delta, finish_reason=None)

                # Final chunk signals end of stream
                yield _make_chunk(chat_id, model_value, "", finish_reason="stop")
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"Streaming error in /v1/chat/completions: {e}", exc_info=True)
                # Yield an error chunk so the client isn't left hanging
                err = {"error": {"message": str(e), "type": "proxy_error"}}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        try:
            response = await gemini_client.generate_content(
                message=final_prompt, model=model_value, files=None
            )
            return convert_to_openai_format(response.text, model_value)
        except Exception as e:
            logger.error(f"Error in /v1/chat/completions endpoint: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error processing chat completion: {str(e)}")
