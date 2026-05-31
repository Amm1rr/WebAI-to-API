import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

@pytest.mark.asyncio
async def test_openapi_legacy_and_specialized_endpoint_metadata():
    """Verify that legacy and specialized endpoints have correct OpenAPI metadata."""
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
    assert "Legacy" in gemini_chat_path["post"]["tags"]

    # Check /translate
    translate_path = schema["paths"].get("/translate")
    assert translate_path is not None, "/translate endpoint missing from OpenAPI schema"
    assert translate_path["post"].get("deprecated") is not True, "/translate should NOT be marked as deprecated"
    assert "Translate Extension Compatibility" in translate_path["post"]["summary"]
    assert "shared global in-memory session" in translate_path["post"]["description"]
    assert "/v1/chat/completions" in translate_path["post"]["description"]
    assert "Translation" in translate_path["post"]["tags"]

    # Check /v1/chat/completions (Primary API)
    chat_path = schema["paths"].get("/v1/chat/completions")
    assert chat_path is not None
    assert "OpenAI-Compatible Chat Completions" in chat_path["post"]["summary"]
    assert "recommended API" in chat_path["post"]["description"]
    assert "Chat" in chat_path["post"]["tags"]

    # Check /v1/models
    models_path = schema["paths"].get("/v1/models")
    assert models_path is not None
    assert "List Available Models" in models_path["get"]["summary"]
    assert "Chat" in models_path["get"]["tags"]

    # Check /v1/auth/status
    auth_status_path = schema["paths"].get("/v1/auth/status")
    assert auth_status_path is not None
    assert "Get Authentication Status" in auth_status_path["get"]["summary"]
    assert "Authentication" in auth_status_path["get"]["tags"]

    # Check /v1/auth/login
    auth_login_path = schema["paths"].get("/v1/auth/login")
    assert auth_login_path is not None
    assert "Trigger Authentication Login" in auth_login_path["post"]["summary"]
    assert "Authentication" in auth_login_path["post"]["tags"]

    # Check /v1beta/models/{model_path}
    v1beta_path = schema["paths"].get("/v1beta/models/{model_path}")
    assert v1beta_path is not None
    assert "Google Generative AI Compatibility Endpoint" in v1beta_path["post"]["summary"]
    assert "Compatibility" in v1beta_path["post"]["tags"]

    # Check /v1/gems
    gems_path = schema["paths"].get("/v1/gems")
    assert gems_path is not None
    assert "List Available Gems" in gems_path["get"]["summary"]
    assert "Utilities" in gems_path["get"]["tags"]

    # Check descriptions and summaries for Legacy
    assert "Legacy" in gemini_path["post"]["summary"]
    assert "Legacy" in gemini_chat_path["post"]["summary"]
    assert "Legacy" in gemini_path["post"]["tags"]
    assert "OpenAI-compatible" in gemini_path["post"]["description"]
    assert "does not survive server restarts" in gemini_chat_path["post"]["description"]
