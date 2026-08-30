import pytest
import json
from app.utils.streaming import (
    convert_chat_completion_to_streaming_chunk,
    simulate_streaming_generator,
)


def test_convert_chat_completion_to_streaming_chunk_preserves_tool_call_shape():
    buffered = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gemini-3-flash",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city":"SF"}',
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }

    chunk = convert_chat_completion_to_streaming_chunk(buffered)

    assert chunk["object"] == "chat.completion.chunk"
    assert chunk["id"] == buffered["id"]
    assert chunk["model"] == buffered["model"]
    assert chunk["created"] == buffered["created"]
    choice = chunk["choices"][0]
    assert "message" not in choice
    expected_delta = {
        **buffered["choices"][0]["message"],
        "tool_calls": [{
            **buffered["choices"][0]["message"]["tool_calls"][0],
            "index": 0,
        }],
    }
    assert choice["delta"] == expected_delta
    assert choice["delta"]["tool_calls"][0]["index"] == 0
    assert choice["finish_reason"] == "tool_calls"
    assert buffered["object"] == "chat.completion"
    assert "message" in buffered["choices"][0]
    assert "index" not in buffered["choices"][0]["message"]["tool_calls"][0]
    assert "usage" not in chunk

@pytest.mark.asyncio
async def test_simulate_streaming_generator():
    """Verify simulate_streaming_generator yields correctly formatted SSE chunks."""
    full_response = {"choices": [{"message": {"content": "Hello world"}}]}
    
    chunks = []
    async for chunk in simulate_streaming_generator(full_response):
        chunks.append(chunk)
        
    assert len(chunks) == 2
    
    # First chunk should be the data JSON
    assert chunks[0].startswith("data: ")
    assert chunks[0].endswith("\n\n")
    data_content = json.loads(chunks[0][6:-2])
    assert data_content == full_response
    
    # Second chunk should be the [DONE] signal
    assert chunks[1] == "data: [DONE]\n\n"
