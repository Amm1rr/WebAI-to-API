# src/app/endpoints/responses.py
"""
OpenAI Responses API endpoint — POST /v1/responses

Implements the subset of the Responses API that Home Assistant uses when sending
images via the ai_task / openai_conversation integration.

Request format (HA sends):
    {
      "model": "gemini-3-pro-image-preview",
      "input": [
        {"type": "message", "role": "developer", "content": "You are helpful"},
        {"type": "message", "role": "user", "content": [
          {"type": "input_text",  "text": "What do you see?"},
          {"type": "input_image", "image_url": "data:image/jpeg;base64,...", "detail": "auto"}
        ]}
      ],
      "instructions": "Optional system prompt shorthand",
      "stream": true,
      "store": false,
      "max_output_tokens": 150
    }

Streaming response format (SSE):
    response.created → response.output_item.added → response.content_part.added
    → response.output_text.delta (one or more) → response.output_text.done
    → response.output_item.done → response.completed
"""

import json
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.logger import logger
from app.services.gemini_client import GeminiClientNotInitializedError, get_gemini_client
from app.utils.image_utils import (
    cleanup_temp_files,
    get_temp_dir,
    serialize_response_images,
)

# Reuse model resolution and content extraction from chat.py
from app.endpoints.chat import _resolve_model, _extract_multimodal_content, _get_cookies

router = APIRouter()


# ---------------------------------------------------------------------------
# Response object builders
# ---------------------------------------------------------------------------

def _make_response_id() -> str:
    return f"resp_{uuid.uuid4().hex[:24]}"


def _make_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _build_response_base(resp_id: str, model_value: str, status: str, output: list) -> dict:
    return {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "model": model_value,
        "output": output,
        "status": status,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


# ---------------------------------------------------------------------------
# Streaming SSE helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_responses_api(text: str, model_value: str, images: list):
    """
    Emit the full OpenAI Responses API SSE event sequence for a completed response.

    Since gemini-webapi returns the full text at once (not token-by-token), we
    emit a single delta containing all text, then close the stream properly so
    HA parses the response correctly.
    """
    resp_id = _make_response_id()
    msg_id = _make_message_id()
    created = int(time.time())

    # Append image markdown to text if images were generated
    content_text = text
    if images:
        md_links = "\n".join(f"![{img['title']}]({img['url']})" for img in images)
        content_text = f"{text}\n\n{md_links}".strip()

    # 1. response.created
    yield _sse("response.created", {
        "type": "response.created",
        "response": _build_response_base(resp_id, model_value, "in_progress", []),
    })

    # 2. response.output_item.added
    yield _sse("response.output_item.added", {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {
            "type": "message",
            "id": msg_id,
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        },
    })

    # 3. response.content_part.added
    yield _sse("response.content_part.added", {
        "type": "response.content_part.added",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": "", "annotations": []},
    })

    # 4. response.output_text.delta  (single chunk — full text)
    yield _sse("response.output_text.delta", {
        "type": "response.output_text.delta",
        "output_index": 0,
        "content_index": 0,
        "delta": content_text,
    })

    # 5. response.output_text.done
    yield _sse("response.output_text.done", {
        "type": "response.output_text.done",
        "output_index": 0,
        "content_index": 0,
        "text": content_text,
    })

    # 6. response.output_item.done
    completed_item = {
        "type": "message",
        "id": msg_id,
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": content_text, "annotations": []}],
    }
    yield _sse("response.output_item.done", {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": completed_item,
    })

    # 7. response.completed
    completed_response = _build_response_base(resp_id, model_value, "completed", [completed_item])
    if images:
        completed_response["images"] = images  # extension field
    yield _sse("response.completed", {
        "type": "response.completed",
        "response": completed_response,
    })


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@router.post("/v1/responses")
async def create_response(request: dict):
    """
    OpenAI Responses API — used by Home Assistant's openai_conversation integration
    when sending camera images for analysis via the ai_task / AI task agent.

    Supports:
    - ``input_text`` and ``input_image`` content parts (Responses API format)
    - ``text`` and ``image_url`` content parts (Chat Completions format, for compatibility)
    - Base64 data URIs, public image URLs, and ``file://`` uploaded file references
    - ``instructions`` field as system prompt shorthand
    - Streaming (``stream: true``) with full SSE event sequence
    - Any model name — unknown names are auto-mapped to the closest Gemini model
    """
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # ── Resolve model ──────────────────────────────────────────────
    gemini_model = _resolve_model(request.get("model"))
    model_value = gemini_model.value
    is_stream = bool(request.get("stream", False))

    # ── Parse input array ──────────────────────────────────────────
    input_items = request.get("input", [])
    if not input_items and not request.get("instructions"):
        raise HTTPException(status_code=400, detail="No input provided.")

    conversation_parts: List[str] = []
    all_file_paths: List[Path] = []
    temp_file_paths: List[Path] = []

    # Optional top-level system prompt shorthand
    instructions = request.get("instructions", "")
    if instructions:
        conversation_parts.append(f"System: {instructions}")

    for item in input_items:
        if not isinstance(item, dict):
            continue

        # Only handle message items (ignore function_call, etc.)
        if item.get("type") != "message":
            continue

        role = item.get("role", "user")
        raw_content = item.get("content", "")

        text, file_paths = await _extract_multimodal_content(raw_content)

        # Track temp files for cleanup
        for fp in file_paths:
            if str(fp).startswith(str(get_temp_dir())):
                temp_file_paths.append(fp)
        all_file_paths.extend(file_paths)

        if not text:
            continue

        # Map role names — Responses API uses "developer" for system
        if role in ("system", "developer"):
            conversation_parts.append(f"System: {text}")
        elif role == "user":
            conversation_parts.append(f"User: {text}")
        elif role == "assistant":
            conversation_parts.append(f"Assistant: {text}")

    if not conversation_parts:
        raise HTTPException(status_code=400, detail="No valid messages found in input.")

    final_prompt = "\n\n".join(conversation_parts)
    files_arg = all_file_paths if all_file_paths else None

    try:
        response = await gemini_client.generate_content(
            message=final_prompt,
            model=model_value,
            files=files_arg,
        )

        images = await serialize_response_images(
            response, gemini_cookies=_get_cookies(gemini_client)
        )

        if is_stream:
            return StreamingResponse(
                _stream_responses_api(response.text, model_value, images),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # Non-streaming response
        resp_id = _make_response_id()
        msg_id = _make_message_id()
        content_text = response.text
        if images:
            md_links = "\n".join(f"![{img['title']}]({img['url']})" for img in images)
            content_text = f"{content_text}\n\n{md_links}".strip()

        result = _build_response_base(
            resp_id, model_value, "completed",
            [{
                "type": "message",
                "id": msg_id,
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": content_text, "annotations": []}],
            }]
        )
        if images:
            result["images"] = images
        if response.thoughts:
            result["thoughts"] = response.thoughts
        return result

    except Exception as e:
        err_str = str(e)
        err_lower = err_str.lower()
        if "auth" in err_lower or "cookie" in err_lower:
            logger.error(f"[/v1/responses] Auth error: {e}")
            raise HTTPException(status_code=401, detail=f"Gemini authentication failed: {err_str}")
        elif "zombie" in err_lower or "parse" in err_lower or "stalled" in err_lower:
            logger.error(f"[/v1/responses] Stream error after retries (model={model_value}): {e}")
            raise HTTPException(status_code=503, detail="Gemini stream temporarily unavailable — please retry")
        else:
            logger.error(f"[/v1/responses] Unexpected error (model={model_value}): {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error: {err_str}")

    finally:
        cleanup_temp_files(temp_file_paths)
