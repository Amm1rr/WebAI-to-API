import json
from typing import AsyncGenerator, Any


def convert_chat_completion_to_streaming_chunk(completion: dict) -> dict:
    """Convert one buffered Chat Completions response into one SSE chunk."""
    chunk = {**completion, "object": "chat.completion.chunk"}
    chunk["choices"] = []
    for choice in completion.get("choices", []):
        streaming_choice = dict(choice)
        message = streaming_choice.pop("message", None)
        delta = dict(message) if isinstance(message, dict) else {}
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list) and len(tool_calls) == 1 and isinstance(tool_calls[0], dict):
            delta["tool_calls"] = [{**tool_calls[0], "index": 0}]
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
