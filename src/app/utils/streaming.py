import json
from typing import AsyncGenerator, Any

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
