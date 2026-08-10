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
        models = await provider.list_models(allow_stale=True)
        assert models == []
        assert provider._refresh_task is None
        
        models_accurate = await provider.list_models(allow_stale=False)
        assert models_accurate == []

@pytest.mark.asyncio
async def test_atlas_list_models_non_blocking_fast_path(mock_atlas_client, atlas_provider):
    """Verify allow_stale=True returns fallback immediately and schedules refresh."""
    mock_atlas_client.list_models.return_value = [{"id": "live-model"}]
    
    # 1. Fast call returns fallback immediately
    models = await atlas_provider.list_models(allow_stale=True)
    assert len(models) == 1
    assert models[0]["id"] == "atlas/MiniMaxAI/MiniMax-M2"
    assert atlas_provider._refresh_task is not None
    
    await atlas_provider._refresh_task
    
    # 2. Next call returns live models
    models = await atlas_provider.list_models(allow_stale=True)
    assert models[0]["id"] == "atlas/live-model"

@pytest.mark.asyncio
async def test_atlas_list_models_blocking_accurate_path(mock_atlas_client, atlas_provider):
    """Verify allow_stale=False awaits live discovery."""
    mock_atlas_client.list_models.return_value = [{"id": "live-model"}]
    
    # Accurate call awaits refresh and returns live models immediately
    models = await atlas_provider.list_models(allow_stale=False)
    assert len(models) == 1
    assert models[0]["id"] == "atlas/live-model"
    assert mock_atlas_client.list_models.call_count == 1

@pytest.mark.asyncio
async def test_atlas_list_models_stale_while_revalidate_ui(mock_atlas_client, atlas_provider):
    """Verify UI path uses stale cache while refreshing."""
    # Setup: Populate cache
    mock_atlas_client.list_models.return_value = [{"id": "old-model"}]
    await atlas_provider.list_models(allow_stale=False)
    
    # Expire cache
    atlas_provider._cache_timestamp -= (atlas_provider._CACHE_TTL + 1)
    
    # Fast call: should return 'old-model' immediately and trigger refresh for 'new-model'
    mock_atlas_client.list_models.return_value = [{"id": "new-model"}]
    models = await atlas_provider.list_models(allow_stale=True)
    
    assert models[0]["id"] == "atlas/old-model"
    assert atlas_provider._refresh_task is not None
    
    await atlas_provider._refresh_task
    models = await atlas_provider.list_models(allow_stale=True)
    assert models[0]["id"] == "atlas/new-model"

@pytest.mark.asyncio
async def test_atlas_list_models_accurate_discovery_failure_fallback(mock_atlas_client, atlas_provider):
    """Verify accurate path returns fallback if discovery fails and no cache exists."""
    mock_atlas_client.list_models.side_effect = Exception("API Down")
    
    # Call returns fallback after failed await
    models = await atlas_provider.list_models(allow_stale=False)
    assert models[0]["id"] == "atlas/MiniMaxAI/MiniMax-M2"

@pytest.mark.asyncio
async def test_atlas_list_models_accurate_discovery_failure_stale_fallback(mock_atlas_client, atlas_provider):
    """Verify accurate path returns stale cache if discovery fails but cache exists."""
    # 1. Populate cache
    mock_atlas_client.list_models.return_value = [{"id": "old-model"}]
    await atlas_provider.list_models(allow_stale=False)
    
    # 2. Expire cache
    atlas_provider._cache_timestamp -= (atlas_provider._CACHE_TTL + 1)
    
    # 3. Discovery fails during accurate call
    mock_atlas_client.list_models.side_effect = Exception("API Down")
    models = await atlas_provider.list_models(allow_stale=False)
    
    # Returns stale cache as best-effort
    assert models[0]["id"] == "atlas/old-model"

@pytest.mark.asyncio
async def test_atlas_list_models_concurrent_refresh(mock_atlas_client, atlas_provider):
    """Verify concurrent calls do not schedule multiple refresh tasks."""
    mock_atlas_client.list_models.return_value = [{"id": "m1"}]
    
    # Trigger first call (fast)
    await atlas_provider.list_models(allow_stale=True)
    task1 = atlas_provider._refresh_task
    
    # Trigger second call (fast)
    await atlas_provider.list_models(allow_stale=True)
    task2 = atlas_provider._refresh_task
    
    assert task1 is task2
    
    await task1
    assert mock_atlas_client.list_models.call_count == 1
    
    # Expire success cache (1 hour) using the active TTL
    atlas_provider._cache_timestamp -= (atlas_provider._current_ttl + 10)
    
    # Call 3: triggers retry
    await atlas_provider.list_models(allow_stale=True)
    assert atlas_provider._refresh_task is not None

@pytest.mark.asyncio
async def test_atlas_list_models_discovery_failure_retry_behavior(mock_atlas_client, atlas_provider):
    """Verify fallback model is used and retried after _FALLBACK_CACHE_TTL."""
    mock_atlas_client.list_models.side_effect = Exception("API Down")
    
    # Call 1: triggers refresh (fails), returns fallback
    await atlas_provider.list_models(allow_stale=False)
    
    assert atlas_provider._model_cache[0]["id"] == "atlas/MiniMaxAI/MiniMax-M2"
    assert atlas_provider._current_ttl == atlas_provider._FALLBACK_CACHE_TTL
    
    # Call 2: cache is still valid (within FALLBACK_CACHE_TTL), no new refresh
    mock_atlas_client.list_models.reset_mock()
    await atlas_provider.list_models(allow_stale=True)
    assert mock_atlas_client.list_models.call_count == 0
    
    # Expire fallback cache (5 mins) using active TTL
    atlas_provider._cache_timestamp -= (atlas_provider._current_ttl + 10)
    
    # Call 3: triggers retry
    await atlas_provider.list_models(allow_stale=True)
    assert atlas_provider._refresh_task is not None

@pytest.mark.asyncio
async def test_atlas_provider_close_cancels_task(mock_atlas_client, atlas_provider):
    """Verify that close() cancels a pending refresh task."""
    # Use a future to control the execution and ensure create_task returns something manageable
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    
    async def slow_refresh():
        try:
            await future
        except asyncio.CancelledError:
            raise
    
    mock_atlas_client.list_models.side_effect = slow_refresh
    
    # We need allow_stale=True to trigger the background task without awaiting it
    await atlas_provider.list_models(allow_stale=True)
    task = atlas_provider._refresh_task
    assert task is not None
    assert not task.done()
    
    await atlas_provider.close()
    assert task.cancelled()
    # Cleanup
    if not future.done():
        future.set_result(None)
