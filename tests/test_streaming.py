import pytest
import json
from app.utils.streaming import simulate_streaming_generator

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
