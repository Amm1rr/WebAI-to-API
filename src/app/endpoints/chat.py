# src/app/endpoints/chat.py
import json
import time
from typing import Optional
from fastapi import APIRouter, HTTPException
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
        response = await session_manager.get_response(request.model, request.message, request.files)
        return {"response": response.text}
    except Exception as e:
        logger.error(f"Error in /translate endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during translation: {str(e)}")


def _build_tools_prompt(tools: list) -> str:
    """Convert OpenAI tool definitions to a system prompt for Gemini."""
    declarations = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            declarations.append(t["function"])
    if not declarations:
        return ""
    lines = [
        "You have access to the following tools. When you want to call a tool, respond with "
        "ONLY a JSON object in this exact format, with no other text before or after:\n"
        '{"tool_call": {"name": "<tool_name>", "arguments": {<arguments>}}}\n',
        "Available tools:",
    ]
    for fn in declarations:
        lines.append(f"- {fn['name']}: {fn.get('description', '')}")
        if fn.get("parameters"):
            lines.append(f"  Parameters: {json.dumps(fn['parameters'])}")
    return "\n".join(lines)


def _parse_tool_call(text: str) -> Optional[dict]:
    """Extract a tool_call JSON object from model response text."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == '{':
            try:
                obj, _ = decoder.raw_decode(text, i)
                if isinstance(obj, dict) and "tool_call" in obj:
                    return obj["tool_call"]
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def convert_to_openai_format(response_text: str, model: str, stream: bool = False, tool_call: Optional[dict] = None):
    ts = int(time.time())
    choice_key = "delta" if stream else "message"
    
    if tool_call:
        args = tool_call.get("arguments", {})
        content = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{ts}",
                "type": "function",
                "function": {
                    "name": tool_call.get("name", ""),
                    "arguments": json.dumps(args) if isinstance(args, dict) else args,
                },
            }],
        }
        return {
            "id": f"chatcmpl-{ts}",
            "object": "chat.completion.chunk" if stream else "chat.completion",
            "created": ts,
            "model": model,
            "choices": [{
                "index": 0,
                choice_key: content,
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    return {
        "id": f"chatcmpl-{ts}",
        "object": "chat.completion.chunk" if stream else "chat.completion",
        "created": ts,
        "model": model,
        "choices": [{
            "index": 0,
            choice_key: {
                "role": "assistant",
                "content": response_text,
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@router.get("/v1/models")
async def list_models():
    from gemini_webapi.constants import Model
    ts = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": model.model_name,
                "object": "model",
                "created": ts,
                "owned_by": "google",
            }
            for model in Model
            if model != Model.UNSPECIFIED
        ],
    }


@router.post("/v1/chat/completions")
async def chat_completions(request: OpenAIChatRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    is_stream = request.stream if request.stream is not None else False

    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")

    conversation_parts = []
    
    # Extract tools prompt
    tools_prompt = _build_tools_prompt(request.tools) if request.tools else ""

    # Merge tools prompt with system message if possible, otherwise prepend it
    system_msg_index = -1
    for i, msg in enumerate(request.messages):
        if msg.get("role") == "system":
            system_msg_index = i
            break

    if tools_prompt:
        if system_msg_index != -1:
            # Append to existing system message
            orig_content = request.messages[system_msg_index].get("content") or ""
            request.messages[system_msg_index]["content"] = f"{orig_content}\n\n{tools_prompt}".strip()
        else:
            # No system message, add one at the beginning
            conversation_parts.append(tools_prompt)

    for msg in request.messages:
        role = msg.get("role", "user")
        content = msg.get("content") or ""

        if role == "system":
            conversation_parts.append(f"System: {content}")
        elif role == "user":
            conversation_parts.append(f"User: {content}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    conversation_parts.append(
                        f"Assistant called tool {fn.get('name')}: {fn.get('arguments', '')}"
                    )
            elif content:
                conversation_parts.append(f"Assistant: {content}")
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            conversation_parts.append(f"Tool result [{tool_call_id}]: {content}")

    if not conversation_parts:
        raise HTTPException(status_code=400, detail="No valid messages found.")

    final_prompt = "\n\n".join(conversation_parts)

    if not request.model:
        raise HTTPException(status_code=400, detail="Model not specified in the request.")

    try:
        response = await gemini_client.generate_content(message=final_prompt, model=request.model, files=None)
        logger.debug(f"Gemini raw response: {response.text!r}")
        tool_call = _parse_tool_call(response.text) if request.tools else None
        logger.debug(f"Parsed tool_call: {tool_call}")
        
        openai_response = convert_to_openai_format(response.text, request.model, is_stream, tool_call)
        
        if is_stream:
            from fastapi.responses import StreamingResponse
            async def sse_stream():
                yield f"data: {json.dumps(openai_response)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(sse_stream(), media_type="text/event-stream")
            
        return openai_response
    except Exception as e:
        logger.error(f"Error in /v1/chat/completions endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing chat completion: {str(e)}")
