import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.factory import ProviderFactory
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.atlas import AtlasProvider

@pytest.mark.asyncio
async def test_list_models_endpoint(mocker):
    """Verify /v1/models filters legacy Playwright Gemini aliases from discovery."""
    # Mock providers to return deterministic model lists
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.list_models = mocker.AsyncMock(return_value=[
        {"id": "gemini-model", "object": "model"},
        {"id": "playwright/gemini-3.5-flash", "object": "model"},
        {"id": "playwright/gemini-3.1-pro", "object": "model"},
        {"id": "playwright/gemini-3.1-flash-lite", "object": "model"},
        {"id": "playwright/gemini/gemini-3.5-flash", "object": "model"},
        {"id": "playwright/gemini/gemini-3.1-pro", "object": "model"},
        {"id": "playwright/gemini/gemini-3.1-flash-lite", "object": "model"},
    ])
    
    mock_atlas = mocker.Mock(spec=AtlasProvider)
    mock_atlas.list_models = mocker.AsyncMock(return_value=[{"id": "atlas-model", "object": "model"}])
    
    # Patch ProviderFactory._registry to use our mocks indirectly
    mocker.patch.dict(ProviderFactory._registry, {
        "gemini": lambda: mock_gemini,
        "atlas": lambda: mock_atlas
    })
    # Reset instances to force factory to use our patched registry
    ProviderFactory._instances = {}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/models")
    
    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    model_ids = [m["id"] for m in data["data"]]
    assert "gemini-model" in model_ids
    assert "atlas-model" in model_ids
    assert "playwright/gemini/gemini-3.5-flash" in model_ids
    assert "playwright/gemini/gemini-3.1-pro" in model_ids
    assert "playwright/gemini/gemini-3.1-flash-lite" in model_ids
    assert "playwright/gemini-3.5-flash" not in model_ids
    assert "playwright/gemini-3.1-pro" not in model_ids
    assert "playwright/gemini-3.1-flash-lite" not in model_ids

@pytest.mark.asyncio
async def test_chat_completions_endpoint_gemini(mocker):
    """Verify /v1/chat/completions delegates to the correct provider and returns OpenAI format."""
    mock_response = {"choices": [{"message": {"content": "Mocked Gemini response"}}]}
    
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.chat_completions = mocker.AsyncMock(return_value=mock_response)
    
    # Patch factory to return our mock provider
    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_gemini, "gemini-3-flash"))

    payload = {
        "model": "gemini-3-flash",
        "messages": [{"role": "user", "content": "Hello"}]
    }
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/chat/completions", json=payload)
    
    assert response.status_code == 200
    assert response.json() == mock_response
    mock_gemini.chat_completions.assert_called_once()

@pytest.mark.asyncio
async def test_chat_completions_endpoint_atlas(mocker):
    """Verify /v1/chat/completions delegates to Atlas when requested."""
    mock_response = {"choices": [{"message": {"content": "Mocked Atlas response"}}]}
    
    mock_atlas = mocker.Mock(spec=AtlasProvider)
    mock_atlas.chat_completions = mocker.AsyncMock(return_value=mock_response)
    
    # Patch factory to return our mock provider
    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_atlas, "MiniMax-M2"))

    payload = {
        "model": "atlas/MiniMax-M2",
        "messages": [{"role": "user", "content": "Hello"}]
    }
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/chat/completions", json=payload)
    
    assert response.status_code == 200
    assert response.json() == mock_response
    mock_atlas.chat_completions.assert_called_once()


@pytest.mark.asyncio
async def test_translate_endpoint_uses_temporary_gemini_requests(mocker):
    """Verify /translate forwards Gemini requests with temporary=True."""
    mock_response = mocker.Mock()
    mock_response.text = "Translated response"

    mock_client = mocker.Mock()
    mock_session_manager = mocker.Mock()
    mock_session_manager.get_response = mocker.AsyncMock(return_value=mock_response)

    mocker.patch("app.endpoints.chat.get_gemini_client", return_value=mock_client)
    mocker.patch("app.endpoints.chat.get_translate_session_manager", return_value=mock_session_manager)

    payload = {
        "model": "gemini-3-flash",
        "message": "Translate this text",
        "files": [],
        "gem": None,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/translate", json=payload)

    assert response.status_code == 200
    assert response.json() == {"response": "Translated response"}
    mock_session_manager.get_response.assert_called_once_with(
        "gemini-3-flash",
        "Translate this text",
        [],
        None,
        temporary=True,
    )


@pytest.mark.asyncio
async def test_delete_conversation_endpoint_gemini(mocker):
    """Verify /v1/conversations/{conversation_id} delegates to Gemini deletion."""
    mock_response = {
        "id": "conv-delete",
        "object": "conversation.deleted",
        "deleted": True,
        "provider": "gemini",
        "backend": "webapi",
    }
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.delete_conversation = mocker.AsyncMock(return_value=mock_response)

    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_gemini, ""))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.delete("/v1/conversations/conv-delete")

    assert response.status_code == 200
    assert response.json() == mock_response
    mock_gemini.delete_conversation.assert_called_once_with("conv-delete")


@pytest.mark.asyncio
async def test_list_conversations_endpoint_gemini_empty(mocker):
    """Verify /v1/conversations delegates to Gemini conversation listing."""
    mock_response = {
        "object": "list",
        "provider": "gemini",
        "backend": "webapi",
        "count": 0,
        "data": [],
    }
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.list_conversations = mocker.AsyncMock(return_value=mock_response)

    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_gemini, ""))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/conversations")

    assert response.status_code == 200
    assert response.json() == mock_response
    mock_gemini.list_conversations.assert_called_once_with()


@pytest.mark.asyncio
async def test_delete_conversations_endpoint_gemini(mocker):
    """Verify DELETE /v1/conversations delegates to Gemini bulk deletion."""
    mock_response = {
        "object": "conversation.bulk_delete",
        "provider": "gemini",
        "backend": "webapi",
        "total": 0,
        "deleted_count": 0,
        "failed_count": 0,
        "skipped_active_count": 0,
        "results": [],
    }
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.delete_conversations = mocker.AsyncMock(return_value=mock_response)

    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_gemini, ""))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.delete("/v1/conversations")

    assert response.status_code == 200
    assert response.json() == mock_response
    mock_gemini.delete_conversations.assert_called_once_with()
