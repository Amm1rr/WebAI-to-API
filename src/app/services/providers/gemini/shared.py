import json
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Any, Union
from pathlib import Path
from fastapi import HTTPException
from app.config import CONFIG
from app.logger import logger
from app.services.providers.exceptions import GeminiProviderOutputError
from .webapi_client import resolve_model_name

# Unrecoverable conversation error codes for Gemini API
UNRECOVERABLE_CONVERSATION_ERROR_CODES = {
    "1097",
}

def resolve_extended_thinking(request: Any) -> bool:
    """Resolve request-scoped extended_thinking: provider override > [Gemini] config > false."""
    provider_options = getattr(request, "provider_options", None)
    gemini_options = getattr(provider_options, "gemini", None) if provider_options else None
    requested = getattr(gemini_options, "extended_thinking", None) if gemini_options else None
    if requested is not None:
        return requested
    return CONFIG.getboolean("Gemini", "extended_thinking", fallback=False)

def is_unknown_model_error(error: ValueError) -> bool:
    """Check if the error is due to an unknown model name."""
    return "Unknown model name" in str(error)


def _ensure_model_available(model: str, resolved_model: Any) -> None:
    if getattr(resolved_model, "is_available", None) is not True:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not available for the current Gemini account or session.",
        )

def validate_model_name(model: Optional[str], gemini_client: Any = None) -> None:
    """Validate a WebAPI model against the initialized runtime model catalog."""
    if not model:
        return

    # Special case for playwright/ prefix used in tests and legacy integrations
    if model.startswith("playwright/"):
        return

    if gemini_client is None:
        from app.services.gemini_client import get_gemini_client
        gemini_client = get_gemini_client()

    try:
        resolved_model = gemini_client.resolve_model(resolve_model_name(model))
        _ensure_model_available(model, resolved_model)
    except ValueError as e:
        if is_unknown_model_error(e):
            raise HTTPException(status_code=400, detail=str(e)) from e
        raise


def validate_direct_webapi_model_name(model: Optional[str], gemini_client: Any) -> None:
    """Validate a model for direct WebAPI endpoints without provider routing."""
    if not model:
        return

    resolved_model = resolve_model_name(model)
    if "/" in resolved_model:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not supported by the Gemini WebAPI endpoint.",
        )

    try:
        resolved = gemini_client.resolve_model(resolved_model)
        _ensure_model_available(model, resolved)
    except ValueError as e:
        if is_unknown_model_error(e):
            raise HTTPException(status_code=400, detail=str(e)) from e
        raise


def ensure_gemini_client_ready(
    gemini_client: Any,
    *,
    unauthenticated_detail: Optional[str] = None,
) -> None:
    """Raise the standard HTTP error for a non-ready Gemini client."""
    account_status = getattr(gemini_client.client, "account_status", None)
    status_name = getattr(account_status, "name", "UNKNOWN") if account_status else "UNKNOWN"
    if status_name == "AVAILABLE":
        return

    logger.warning(f"Gemini client account status is '{status_name}'.")
    if status_name == "UNAUTHENTICATED":
        raise HTTPException(
            status_code=401,
            detail=unauthenticated_detail or "Gemini authentication is required. Please sign in and try again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raise HTTPException(
        status_code=401 if status_name == "UNKNOWN" else 503,
        detail=f"Gemini client is not ready (status: {status_name}).",
    )

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

class ToolCallParseStatus(str, Enum):
    NO_TOOL_CALL = "no_tool_call"
    VALID_TOOL_CALL = "valid_tool_call"
    INVALID_TOOL_CALL = "invalid_tool_call"


@dataclass(frozen=True, slots=True)
class ToolCallParseResult:
    status: ToolCallParseStatus
    tool_call: Optional[dict] = None
    error: Optional[str] = None


def _invalid_tool_call(error: str) -> ToolCallParseResult:
    return ToolCallParseResult(ToolCallParseStatus.INVALID_TOOL_CALL, error=error)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _declared_tool_names(tools: list[dict]) -> set[str]:
    names = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.add(function["name"])
    return names


def _looks_like_tool_envelope(text: str) -> bool:
    return bool(re.match(r'^\{\s*"tool_calls?"\s*:', text))


def _decode_exact_json(text: str) -> tuple[Any, bool]:
    """Decode only a complete JSON value, returning whether format was claimed."""
    stripped = text.strip()
    if not stripped:
        return None, False

    if stripped.startswith("```"):
        match = re.fullmatch(
            r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```",
            stripped,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            return None, False
        payload = match.group(1).strip()
        try:
            return json.loads(
                payload,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonstandard_json_constant,
            ), True
        except (json.JSONDecodeError, ValueError):
            return None, _looks_like_tool_envelope(payload)

    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_nonstandard_json_constant,
    )
    try:
        value, end = decoder.raw_decode(stripped)
    except (json.JSONDecodeError, ValueError):
        return None, _looks_like_tool_envelope(stripped)

    if stripped[end:].strip():
        return None, False
    return value, True


def parse_tool_call(text: str, tools: Optional[List[dict]] = None) -> ToolCallParseResult:
    """Parse one exact model tool envelope without searching inside prose."""
    if not isinstance(text, str):
        return _invalid_tool_call("Gemini returned non-text tool-call output.")
    decoded, exact_json = _decode_exact_json(text)
    if not exact_json:
        return ToolCallParseResult(ToolCallParseStatus.NO_TOOL_CALL)
    if decoded is None:
        return _invalid_tool_call("Gemini returned malformed tool-call JSON.")

    if isinstance(decoded, list):
        if any(
            isinstance(item, dict)
            and (
                "tool_call" in item
                or "tool_calls" in item
                or {"name", "arguments"}.issubset(item)
            )
            for item in decoded
        ):
            return _invalid_tool_call("Multiple or unsupported tool calls were returned.")
        return ToolCallParseResult(ToolCallParseStatus.NO_TOOL_CALL)
    if not isinstance(decoded, dict):
        return ToolCallParseResult(ToolCallParseStatus.NO_TOOL_CALL)

    if "tool_calls" in decoded:
        return _invalid_tool_call("Multiple tool calls are not supported.")
    if "tool_call" not in decoded:
        return ToolCallParseResult(ToolCallParseStatus.NO_TOOL_CALL)
    if set(decoded) != {"tool_call"}:
        return _invalid_tool_call("Tool-call envelope contains unsupported fields.")

    tool_call = decoded["tool_call"]
    if not isinstance(tool_call, dict):
        return _invalid_tool_call("Tool-call envelope must be an object.")
    if set(tool_call) != {"name", "arguments"}:
        return _invalid_tool_call("Tool call must contain only name and arguments.")

    name = tool_call["name"]
    arguments = tool_call["arguments"]
    if not isinstance(name, str) or not name.strip():
        return _invalid_tool_call("Tool call name must be a non-empty string.")
    if not isinstance(arguments, dict):
        return _invalid_tool_call("Tool call arguments must be an object.")
    if tools is not None and name not in _declared_tool_names(tools):
        return _invalid_tool_call(f"Tool call names undeclared tool: {name}.")

    return ToolCallParseResult(ToolCallParseStatus.VALID_TOOL_CALL, tool_call=tool_call)


def _validate_tool_call_for_openai(tool_call: Any) -> tuple[str, str]:
    if not isinstance(tool_call, dict):
        raise GeminiProviderOutputError("Gemini returned malformed tool-call output.")
    if set(tool_call) != {"name", "arguments"}:
        raise GeminiProviderOutputError("Gemini returned malformed tool-call output.")

    name = tool_call["name"]
    arguments = tool_call["arguments"]
    if not isinstance(name, str) or not name.strip() or not isinstance(arguments, dict):
        raise GeminiProviderOutputError("Gemini returned malformed tool-call output.")
    try:
        serialized_arguments = json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise GeminiProviderOutputError("Gemini returned unserializable tool-call arguments.") from error
    return name, serialized_arguments


def convert_to_openai_format(response_text: str, model: str, stream: bool = False, tool_call: Optional[dict] = None):
    """Normalize Gemini response text or tool calls to OpenAI-compatible format."""
    ts = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex}" if tool_call is not None or not stream else f"chatcmpl-{ts}"
    choice_key = "delta" if stream else "message"
    
    if tool_call is not None:
        name, serialized_arguments = _validate_tool_call_for_openai(tool_call)
        tool_call_payload = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{uuid.uuid4().hex}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": serialized_arguments,
                },
            }],
        }
        if stream:
            tool_call_payload["tool_calls"][0]["index"] = 0
        return {
            "id": completion_id,
            "object": "chat.completion.chunk" if stream else "chat.completion",
            "created": ts,
            "model": model,
            "choices": [{
                "index": 0,
                choice_key: tool_call_payload,
                "finish_reason": "tool_calls",
            }],
        }

    return {
        "id": completion_id,
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

def get_gemini_models(
    runtime_models: Optional[List[Any]] = None,
    *,
    include_playwright: bool = True,
) -> List[dict]:
    """Return runtime WebAPI models, optionally followed by Playwright models."""
    ts = int(time.time())

    models = []
    seen_ids = set()
    for model in runtime_models or []:
        model_id = getattr(model, "model_name", None)
        if getattr(model, "is_available", False) is not True or not model_id or model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        models.append({
            "id": model_id,
            "object": "model",
            "created": ts,
            "owned_by": "google",
        })

    if not include_playwright:
        return models

    # Playwright-native models
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


def get_direct_webapi_gemini_models(runtime_models: Optional[List[Any]] = None) -> List[dict]:
    """Return only runtime models valid for direct Gemini WebAPI execution."""
    direct_models = []
    for model in runtime_models or []:
        model_id = getattr(model, "model_name", None)
        if isinstance(model_id, str) and "/" not in resolve_model_name(model_id):
            direct_models.append(model)
    return get_gemini_models(direct_models, include_playwright=False)

def format_files(files: Optional[List[Union[str, Path]]]) -> Optional[List[Path]]:
    """Convert a list of file paths (strings or Path objects) to Path objects."""
    if not files:
        return None
    return [Path(f) if isinstance(f, str) else f for f in files]
