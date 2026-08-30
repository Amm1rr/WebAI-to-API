import json
from typing import AsyncGenerator, Any

from app.services.providers.exceptions import GeminiProviderOutputError


def convert_chat_completion_to_streaming_chunk(completion: dict) -> dict:
    """Convert one buffered Chat Completions response into one SSE chunk."""
    chunk = {**completion, "object": "chat.completion.chunk"}
    chunk["choices"] = []
    for choice in completion.get("choices", []):
        streaming_choice = dict(choice)
        message = streaming_choice.pop("message", None)
        delta = dict(message) if isinstance(message, dict) else {}
        tool_calls = delta.get("tool_calls")
        if streaming_choice.get("finish_reason") == "tool_calls" and tool_calls is None:
            raise GeminiProviderOutputError("Buffered response contains no tool calls.")
        if tool_calls is not None and streaming_choice.get("finish_reason") != "tool_calls":
            raise GeminiProviderOutputError("Buffered response has an invalid tool-call finish reason.")
        if tool_calls is not None:
            if not isinstance(tool_calls, list) or len(tool_calls) != 1 or not isinstance(tool_calls[0], dict):
                raise GeminiProviderOutputError("Buffered response contains malformed tool calls.")
            tool_call = tool_calls[0]
            function = tool_call.get("function")
            arguments = function.get("arguments") if isinstance(function, dict) else None
            if (
                not isinstance(tool_call.get("id"), str)
                or not tool_call["id"]
                or tool_call.get("type") != "function"
                or not isinstance(function, dict)
                or not isinstance(function.get("name"), str)
                or not function["name"].strip()
                or not isinstance(arguments, str)
            ):
                raise GeminiProviderOutputError("Buffered response contains malformed tool calls.")
            try:
                parsed_arguments = json.loads(arguments)
            except (TypeError, ValueError) as error:
                raise GeminiProviderOutputError("Buffered response contains malformed tool calls.") from error
            if not isinstance(parsed_arguments, dict):
                raise GeminiProviderOutputError("Buffered response contains malformed tool calls.")
            delta["tool_calls"] = [{**tool_call, "index": 0}]
        streaming_choice["delta"] = delta
        chunk["choices"].append(streaming_choice)
    return chunk


async def format_sse_chunk(chunk_data: dict) -> str:
    """Format a data dictionary into an OpenAI-compatible SSE chunk."""
    return f"data: {json.dumps(chunk_data)}\n\n"

async def get_done_chunk() -> str:
    """Return the final OpenAI [DONE] signal."""
    return "data: [DONE]\n\n"

async def simulate_streaming_generator(full_response: dict) -> AsyncGenerator[str, None]:
    """
    Yields a single data chunk containing the full response followed by [DONE].
    Used for non-streaming backends that need to satisfy the OpenAI streaming protocol.
    """
    yield await format_sse_chunk(full_response)
    yield await get_done_chunk()
