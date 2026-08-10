import json
import time
from typing import Optional, List, Any, Union
from pathlib import Path
from fastapi import HTTPException
from app.logger import logger
from .webapi_client import resolve_model_name

# Unrecoverable conversation error codes for Gemini API
UNRECOVERABLE_CONVERSATION_ERROR_CODES = {
    "1097",
}

def is_unknown_model_error(error: ValueError) -> bool:
    """Check if the error is due to an unknown model name."""
    return "Unknown model name" in str(error)

def validate_model_name(model: Optional[str]) -> None:
    """Validate the Gemini model name using the official constants."""
    if not model:
        return

    # Special case for playwright/ prefix used in tests and legacy integrations
    if model.startswith("playwright/"):
        return

    from gemini_webapi.constants import Model

    try:
        Model.from_name(resolve_model_name(model))
    except ValueError as e:
        if is_unknown_model_error(e):
            raise HTTPException(status_code=400, detail=str(e)) from e
        raise

def build_tools_prompt(tools: list) -> str:
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

def parse_tool_call(text: str) -> Optional[dict]:
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
    """Normalize Gemini response text or tool calls to OpenAI-compatible format."""
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

# Normalization mapping for Playwright backend: OpenAI ID -> Gemini UI Label
# Keep ONLY runtime-verified and fully implemented direct-select models.
# NOTE: "Thinking" models are deferred until submenu handling is implemented.
PLAYWRIGHT_GEMINI_MODEL_UI_LABELS = {
    "gemini-3.1-pro": "Pro",
    "gemini-3.5-flash": "Flash",
    "gemini-3.1-flash-lite": "Flash-Lite",
}

PLAYWRIGHT_GEMINI_PROVIDER_NAMESPACE = "playwright/gemini"

def get_gemini_models() -> List[dict]:
    """Return the canonical list of supported Gemini models in OpenAI format."""
    from gemini_webapi.constants import Model
    ts = int(time.time())
    
    # 1. Standard WebAPI Models
    models = [
        {
            "id": model.model_name,
            "object": "model",
            "created": ts,
            "owned_by": "google",
        }
        for model in Model
        if model != Model.UNSPECIFIED
    ]
    
    # 2. Playwright-native Models
    for model_id in PLAYWRIGHT_GEMINI_MODEL_UI_LABELS.keys():
        models.append({
            "id": f"playwright/{model_id}",
            "object": "model",
            "created": ts,
            "owned_by": "google",
        })
        models.append({
            "id": f"{PLAYWRIGHT_GEMINI_PROVIDER_NAMESPACE}/{model_id}",
            "object": "model",
            "created": ts,
            "owned_by": "google",
        })
    
    return models

def format_files(files: Optional[List[Union[str, Path]]]) -> Optional[List[Path]]:
    """Convert a list of file paths (strings or Path objects) to Path objects."""
    if not files:
        return None
    return [Path(f) if isinstance(f, str) else f for f in files]
