import asyncio
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
        assert provider._refresh_task is None

@pytest.mark.asyncio
async def test_atlas_list_models_non_blocking_initial(mock_atlas_client, atlas_provider):
    """Verify list_models returns fallback immediately on first call and triggers refresh."""
    mock_atlas_client.list_models.return_value = [{"id": "live-model"}]
    
    # 1. Call returns fallback immediately
    models = await atlas_provider.list_models()
    assert len(models) == 1
    assert models[0]["id"] == "atlas/MiniMaxAI/MiniMax-M2"
    
    # 2. Verify refresh task is scheduled
    assert atlas_provider._refresh_task is not None
    
    # 3. Wait for refresh to complete
    await atlas_provider._refresh_task
    
    # 4. Next call should return live models
    models = await atlas_provider.list_models()
    assert len(models) == 1
    assert models[0]["id"] == "atlas/live-model"

@pytest.mark.asyncio
async def test_atlas_list_models_stale_while_revalidate(mock_atlas_client, atlas_provider):
    """Verify stale cache is returned while refresh happens in background."""
    # Setup: Populate cache
    mock_atlas_client.list_models.return_value = [{"id": "old-model"}]
    await atlas_provider.list_models()
    await atlas_provider._refresh_task
    
    # Expire cache
    atlas_provider._cache_timestamp -= (atlas_provider._CACHE_TTL + 1)
    
    # Next call: should return 'old-model' immediately and trigger refresh for 'new-model'
    mock_atlas_client.list_models.return_value = [{"id": "new-model"}]
    models = await atlas_provider.list_models()
    
    assert models[0]["id"] == "atlas/old-model"
    assert atlas_provider._refresh_task is not None
    
    await atlas_provider._refresh_task
    models = await atlas_provider.list_models()
    assert models[0]["id"] == "atlas/new-model"

@pytest.mark.asyncio
async def test_atlas_list_models_concurrent_refresh(mock_atlas_client, atlas_provider):
    """Verify concurrent calls do not schedule multiple refresh tasks."""
    mock_atlas_client.list_models.return_value = [{"id": "m1"}]
    
    # Trigger first call
    await atlas_provider.list_models()
    task1 = atlas_provider._refresh_task
    
    # Trigger second call immediately
    await atlas_provider.list_models()
    task2 = atlas_provider._refresh_task
    
    assert task1 is task2
    
    # Wait for the task to complete to verify call count
    await task1
    assert mock_atlas_client.list_models.call_count == 1

@pytest.mark.asyncio
async def test_atlas_list_models_discovery_failure_fallback_behavior(mock_atlas_client, atlas_provider):
    """Verify fallback model is used and cached briefly on discovery failure."""
    mock_atlas_client.list_models.side_effect = Exception("API Down")
    
    # Call 1: triggers refresh (fails), returns fallback
    await atlas_provider.list_models()
    await atlas_provider._refresh_task
    
    assert atlas_provider._model_cache[0]["id"] == "atlas/MiniMaxAI/MiniMax-M2"
    
    # Call 2: cache is still valid (within FALLBACK_CACHE_TTL), no new refresh
    mock_atlas_client.list_models.reset_mock()
    await atlas_provider.list_models()
    assert mock_atlas_client.list_models.call_count == 0
    
    # Expire fallback cache (5 mins)
    atlas_provider._cache_timestamp -= (atlas_provider._FALLBACK_CACHE_TTL + 10)
    
    # Call 3: triggers retry
    await atlas_provider.list_models()
    assert atlas_provider._refresh_task is not None

@pytest.mark.asyncio
async def test_atlas_provider_close_cancels_task(mock_atlas_client, atlas_provider):
    """Verify that close() cancels a pending refresh task."""
    async def slow_refresh():
        await asyncio.sleep(10)
    
    mock_atlas_client.list_models.side_effect = slow_refresh
    
    await atlas_provider.list_models()
    task = atlas_provider._refresh_task
    assert task is not None
    assert not task.done()
    
    await atlas_provider.close()
    assert task.cancelled()
