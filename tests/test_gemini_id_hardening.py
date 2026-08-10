import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from app.services.providers.gemini.provider import GeminiProvider
from app.schemas.request import OpenAIChatRequest
from app.services.providers.base_repository import ConversationSnapshot
from datetime import datetime, timezone

@pytest.fixture
def provider():
    return GeminiProvider()

@pytest.fixture
def mock_registry(mocker):
    mock_registry = mocker.Mock()
    mock_repo = mocker.Mock()
    mock_registry.repository = mock_repo
    mocker.patch("app.services.providers.gemini.session_manager.get_gemini_chat_registry", return_value=mock_registry)
    return mock_registry

@pytest.mark.asyncio
async def test_webapi_id_rejected_by_playwright(mocker, provider, mock_registry):
    # 1. Setup a "WebAPI-owned" ID in the mock repository
    owned_id = "webapi-token"
    snapshot = ConversationSnapshot(
        conversation_id=owned_id,
        provider_name="gemini",
        session_state={},
        schema_version=1,
        updated_at=datetime.now(timezone.utc)
    )
    mock_registry.repository.get_snapshot = AsyncMock(return_value=snapshot)
    
    # 2. Request this ID via Playwright
    # We force the adapter selection to Playwright
    mocker.patch.object(provider, "_get_adapter", return_value=provider.playwright_adapter)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="playwright/gemini-3-flash",
        conversation_id=owned_id
    )
    
    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)
    
    assert exc_info.value.status_code == 400
    assert "Incompatible conversation_id for Playwright backend" in exc_info.value.detail

@pytest.mark.asyncio
async def test_snapshot_not_found_on_playwright_ownership_lookup_allows_external_id(mocker, provider, mock_registry):
    from app.services.providers.exceptions import SnapshotNotFoundError
    
    # Mock repository to raise SnapshotNotFoundError
    mock_registry.repository.get_snapshot = AsyncMock(side_effect=SnapshotNotFoundError("Unexpected error"))
    
    # Mock Playwright adapter to succeed
    provider.playwright_adapter.chat_completions = AsyncMock(return_value={"status": "success"})
    mocker.patch.object(provider, "_get_adapter", return_value=provider.playwright_adapter)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="playwright/gemini",
        conversation_id="some-external-id"
    )
    
    # Should not raise exception anymore
    result = await provider.chat_completions(request)
    assert result["status"] == "success"
    provider.playwright_adapter.chat_completions.assert_called_once()

@pytest.mark.asyncio
async def test_prefixed_webapi_id_rejected_by_playwright(mocker, provider, mock_registry):
    # 1. Setup a "WebAPI-owned" ID in the mock repository (with prefix)
    prefixed_id = "wa_token-123"
    snapshot = ConversationSnapshot(
        conversation_id=prefixed_id,
        provider_name="gemini",
        session_state={},
        schema_version=1,
        updated_at=datetime.now(timezone.utc)
    )
    # Repository mock returns snapshot only for the exact prefixed ID
    def side_effect(cid):
        if cid == prefixed_id: return snapshot
        return None
    mock_registry.repository.get_snapshot = AsyncMock(side_effect=side_effect)
    
    # 2. Request this ID via Playwright
    mocker.patch.object(provider, "_get_adapter", return_value=provider.playwright_adapter)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="playwright/gemini",
        conversation_id=prefixed_id
    )
    
    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)
    
    assert exc_info.value.status_code == 400
    assert "Incompatible conversation_id for Playwright backend" in exc_info.value.detail

@pytest.mark.asyncio
async def test_unknown_id_allowed_by_playwright(mocker, provider, mock_registry):
    # 1. Mock repository to return None (ID not owned by WebAPI)
    mock_registry.repository.get_snapshot = AsyncMock(return_value=None)
    
    # 2. Mock Playwright adapter to succeed
    provider.playwright_adapter.chat_completions = AsyncMock(return_value={"status": "success"})
    mocker.patch.object(provider, "_get_adapter", return_value=provider.playwright_adapter)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="playwright/gemini",
        conversation_id="unknown-url-id"
    )
    
    result = await provider.chat_completions(request)
    assert result["status"] == "success"
    # Verify the ID passed to adapter is unchanged
    provider.playwright_adapter.chat_completions.assert_called_once()
    assert provider.playwright_adapter.chat_completions.call_args[0][1] == "unknown-url-id"

@pytest.mark.asyncio
async def test_pw_prefixed_id_stripped_and_allowed_by_playwright(mocker, provider, mock_registry):
    # 1. Mock repository to return None (ID not owned by WebAPI)
    mock_registry.repository.get_snapshot = AsyncMock(return_value=None)
    
    # 2. Mock Playwright adapter
    provider.playwright_adapter.chat_completions = AsyncMock(return_value={"status": "success"})
    mocker.patch.object(provider, "_get_adapter", return_value=provider.playwright_adapter)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="playwright/gemini",
        conversation_id="pw_external-id"
    )
    
    await provider.chat_completions(request)
    # Verify the ID passed to adapter is STRIPPED
    provider.playwright_adapter.chat_completions.assert_called_once()
    assert provider.playwright_adapter.chat_completions.call_args[0][1] == "external-id"

@pytest.mark.asyncio
async def test_wa_prefixed_id_not_stripped_and_allowed_by_webapi(mocker, provider, mock_registry):
    # 1. Setup owned ID
    owned_id = "wa_token-123"
    mock_registry.repository.get_snapshot = AsyncMock(return_value=True)
    
    # 2. Mock WebAPI adapter
    provider.webapi_adapter.chat_completions = AsyncMock(return_value={"status": "success"})
    mocker.patch.object(provider, "_get_adapter", return_value=provider.webapi_adapter)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="gemini-3-flash",
        conversation_id=owned_id
    )
    
    await provider.chat_completions(request)
    # Verify the ID passed to adapter is NOT stripped (to match DB)
    provider.webapi_adapter.chat_completions.assert_called_once()
    assert provider.webapi_adapter.chat_completions.call_args[0][1] == owned_id

@pytest.mark.asyncio
async def test_webapi_id_allowed_by_webapi(mocker, provider, mock_registry):
    # 1. Setup owned ID
    owned_id = "webapi-token"
    mock_registry.repository.get_snapshot = AsyncMock(return_value=True) # just truthy
    
    # 2. Mock WebAPI adapter
    provider.webapi_adapter.chat_completions = AsyncMock(return_value={"status": "success"})
    mocker.patch.object(provider, "_get_adapter", return_value=provider.webapi_adapter)
    
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="gemini-3-flash",
        conversation_id=owned_id
    )
    
    result = await provider.chat_completions(request)
    assert result["status"] == "success"
    provider.webapi_adapter.chat_completions.assert_called_once()
    assert provider.webapi_adapter.chat_completions.call_args[0][1] == owned_id
