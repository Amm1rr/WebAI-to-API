import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.factory import ProviderFactory
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.atlas import AtlasProvider

@pytest.mark.asyncio
async def test_list_models_endpoint(mocker):
    """Verify /v1/models returns models from all registered providers."""
    # Mock providers to return deterministic model lists
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.list_models = mocker.AsyncMock(return_value=[{"id": "gemini-model", "object": "model"}])
    
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
