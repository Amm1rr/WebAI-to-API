import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock, ANY
from app.services.providers.atlas.provider import AtlasProvider
from app.services.providers.atlas.client import AtlasClient, AtlasClientError, AtlasClientNotConfiguredError

@pytest.fixture
def mock_atlas_client():
    with patch("app.services.providers.atlas.provider.get_atlas_client") as mock_get:
        client = MagicMock(spec=AtlasClient)
        mock_get.return_value = client
        yield client

@pytest.fixture
def atlas_provider():
    return AtlasProvider()

@pytest.mark.asyncio
async def test_atlas_client_url_resolution():
    """Verify that AtlasClient resolves URLs correctly regardless of trailing slash in base_url."""
    
    # Case 1: Base URL without trailing slash
    client_no_slash = AtlasClient(api_key="test", base_url="https://api.atlascloud.ai/v1")
    assert client_no_slash.base_url == "https://api.atlascloud.ai/v1/"
    
    # Case 2: Base URL with trailing slash
    client_with_slash = AtlasClient(api_key="test", base_url="https://api.atlascloud.ai/v1/")
    assert client_with_slash.base_url == "https://api.atlascloud.ai/v1/"

    # Mock httpx.AsyncClient to verify request paths
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_response = MagicMock()
        mock_response.is_error = False
        mock_response.json.return_value = {"choices": []}
        mock_client.send = AsyncMock(return_value=mock_response)
        
        await client_no_slash.chat_completions(messages=[], model="test")
        
        # Verify base_url passed to httpx (must end in slash for relative "chat/completions" to work)
        mock_client_cls.assert_called_with(
            base_url="https://api.atlascloud.ai/v1/",
            headers=ANY,
            timeout=ANY
        )
        # Verify relative path
        mock_client.build_request.assert_called_with("POST", "chat/completions", json=ANY)

@pytest.mark.asyncio
async def test_atlas_client_list_models_url_and_parsing():
    """Verify list_models uses correct relative path and parses Atlas JSON data envelope."""
    client = AtlasClient(api_key="test", base_url="https://api.atlascloud.ai/v1")
    
    with patch("httpx.AsyncClient") as mock_client_cls:
        # Mock the context manager __aenter__ return value
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_response = MagicMock()
        mock_response.is_error = False
        # Atlas envelope: { "code": 200, "data": [...] }
        mock_response.json.return_value = {"data": [{"id": "m1"}]}
        mock_client.get = AsyncMock(return_value=mock_response)
        
        models = await client.list_models()
        
        # Verify normalization and relative path
        mock_client_cls.assert_called_with(
            base_url="https://api.atlascloud.ai/v1/",
            headers=ANY,
            timeout=ANY
        )
        mock_client.get.assert_called_with("models")
        
        # Verify envelope extraction
        assert models == [{"id": "m1"}]

@pytest.mark.asyncio
async def test_atlas_list_models_unconfigured():
    """Verify list_models returns [] when Atlas is not configured."""
    with patch("app.services.providers.atlas.provider.get_atlas_client", side_effect=AtlasClientNotConfiguredError("Missing key")):
        provider = AtlasProvider()
        models = await provider.list_models()
        assert models == []

@pytest.mark.asyncio
async def test_atlas_list_models_success(mock_atlas_client, atlas_provider):
    """Verify successful dynamic discovery with ID prefixing and normalization."""
    mock_atlas_client.list_models.return_value = [
        {"id": "deepseek-ai/DeepSeek-V3", "created": 12345},
        {"id": "atlas/already-prefixed", "created": 67890}
    ]
    
    models = await atlas_provider.list_models()
    
    assert len(models) == 2
    assert models[0]["id"] == "atlas/deepseek-ai/DeepSeek-V3"
    assert models[1]["id"] == "atlas/already-prefixed" # No double prefix
    assert all(m["owned_by"] == "atlascloud" for m in models)

@pytest.mark.asyncio
async def test_atlas_list_models_cache_success(mock_atlas_client, atlas_provider):
    """Verify success results are cached for _CACHE_TTL."""
    mock_atlas_client.list_models.return_value = [{"id": "model-1"}]
    
    await atlas_provider.list_models()
    await atlas_provider.list_models()
    assert mock_atlas_client.list_models.call_count == 1

@pytest.mark.asyncio
async def test_atlas_list_models_discovery_failure_fallback_and_cache(mock_atlas_client, atlas_provider):
    """Verify fallback model is returned on discovery failure and cached for _FALLBACK_CACHE_TTL."""
    mock_atlas_client.list_models.side_effect = Exception("API Down")
    
    # First call: triggers discovery, fails, sets fallback
    models = await atlas_provider.list_models()
    assert len(models) == 1
    assert models[0]["id"] == "atlas/MiniMaxAI/MiniMax-M2"
    assert mock_atlas_client.list_models.call_count == 1
    
    # Second call: should use fallback cache
    await atlas_provider.list_models()
    assert mock_atlas_client.list_models.call_count == 1
    
    # Expire fallback cache (5 mins) but not success cache (1 hour)
    atlas_provider._cache_timestamp -= (atlas_provider._FALLBACK_CACHE_TTL + 10)
    
    # Third call: should retry discovery
    await atlas_provider.list_models()
    assert mock_atlas_client.list_models.call_count == 2
