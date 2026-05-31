import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

@pytest.mark.asyncio
async def test_openapi_deprecated_endpoints():
    """Verify that legacy endpoints are marked as deprecated in the OpenAPI schema."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/openapi.json")
    
    assert response.status_code == 200
    schema = response.json()
    
    # Check /gemini
    gemini_path = schema["paths"].get("/gemini")
    assert gemini_path is not None, "/gemini endpoint missing from OpenAPI schema"
    assert gemini_path["post"].get("deprecated") is True, "/gemini should be marked as deprecated"
    
    # Check /gemini-chat
    gemini_chat_path = schema["paths"].get("/gemini-chat")
    assert gemini_chat_path is not None, "/gemini-chat endpoint missing from OpenAPI schema"
    assert gemini_chat_path["post"].get("deprecated") is True, "/gemini-chat should be marked as deprecated"

    # Check descriptions and summaries
    assert "Legacy" in gemini_path["post"]["summary"]
    assert "Legacy" in gemini_chat_path["post"]["summary"]
    assert "OpenAI-compatible" in gemini_path["post"]["description"]
    assert "does not survive server restarts" in gemini_chat_path["post"]["description"]
