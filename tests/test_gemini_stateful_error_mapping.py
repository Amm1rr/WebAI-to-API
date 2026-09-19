"""Stateful buffered Gemini WebAPI error mapping (A) + duplicate-log removal (B)."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from gemini_webapi.exceptions import (
    APIError,
    AuthError,
    GeminiError,
    ModelInvalidError,
    TemporarilyBlockedError,
    TimeoutError as GeminiTimeoutError,
    UsageLimitExceededError,
)

from app.schemas.request import OpenAIChatRequest
from app.services.providers.exceptions import GeminiProviderOutputError
from app.services.providers.gemini import stateless_chat
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.gemini.session_manager import SessionManager, SessionRegistry
from app.services.providers.gemini.shared import translate_gemini_provider_error


@pytest.fixture
def provider():
    return GeminiProvider()


def _install_buffered_failure(mocker, install_gemini_client, failure, conversation_id="conv-err"):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash", is_available=True
    )
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)
    mock_manager.get_response_stateful = mocker.AsyncMock(side_effect=failure)
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()
    install_gemini_client(mock_client)
    mocker.patch(
        "app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry",
        return_value=mock_registry,
    )
    return mock_registry


def _request(conversation_id="conv-err"):
    return OpenAIChatRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="gemini-3-flash",
        conversation_id=conversation_id,
        stream=False,
    )


async def _assert_buffered_status(
    mocker, provider, install_gemini_client, failure, status, detail=None
):
    _install_buffered_failure(mocker, install_gemini_client, failure)
    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(_request())
    assert exc_info.value.status_code == status
    if detail is not None:
        assert exc_info.value.detail == detail
    return exc_info


@pytest.mark.asyncio
async def test_stateful_buffered_api_error_maps_502_without_raw_text(
    mocker, provider, install_gemini_client
):
    exc_info = await _assert_buffered_status(
        mocker,
        provider,
        install_gemini_client,
        APIError("provider error"),
        502,
        "Gemini WebAPI provider request failed.",
    )
    assert "provider error" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_stateful_buffered_generic_gemini_error_maps_502(
    mocker, provider, install_gemini_client
):
    await _assert_buffered_status(
        mocker,
        provider,
        install_gemini_client,
        GeminiError("boom"),
        502,
        "Gemini WebAPI provider request failed.",
    )


@pytest.mark.asyncio
async def test_stateful_buffered_auth_error_maps_503(mocker, provider, install_gemini_client):
    await _assert_buffered_status(
        mocker,
        provider,
        install_gemini_client,
        AuthError("no auth"),
        503,
        "Gemini WebAPI authentication is unavailable.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [asyncio.TimeoutError(), GeminiTimeoutError("timed out")],
    ids=["asyncio-timeout", "gemini-timeout"],
)
async def test_stateful_buffered_timeout_maps_504(
    mocker, provider, install_gemini_client, failure
):
    await _assert_buffered_status(
        mocker,
        provider,
        install_gemini_client,
        failure,
        504,
        "Gemini WebAPI request timed out.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "detail"),
    [
        (UsageLimitExceededError("limit"), "Gemini WebAPI usage limit exceeded."),
        (TemporarilyBlockedError("blocked"), "Gemini WebAPI request is temporarily blocked."),
    ],
    ids=["usage-limit", "temporarily-blocked"],
)
async def test_stateful_buffered_rate_limits_map_429(
    mocker, provider, install_gemini_client, failure, detail
):
    await _assert_buffered_status(mocker, provider, install_gemini_client, failure, 429, detail)


@pytest.mark.asyncio
async def test_stateful_buffered_model_invalid_maps_502(
    mocker, provider, install_gemini_client
):
    await _assert_buffered_status(
        mocker,
        provider,
        install_gemini_client,
        ModelInvalidError("bad model"),
        502,
        "Gemini WebAPI rejected the requested model.",
    )


@pytest.mark.asyncio
async def test_stateful_buffered_malformed_tool_output_maps_502(
    mocker, provider, install_gemini_client
):
    await _assert_buffered_status(
        mocker,
        provider,
        install_gemini_client,
        GeminiProviderOutputError("bad tool output"),
        502,
        "Gemini WebAPI returned malformed tool output.",
    )


@pytest.mark.asyncio
async def test_stateful_buffered_unrecoverable_conversation_still_410(
    mocker, provider, install_gemini_client
):
    _install_buffered_failure(
        mocker,
        install_gemini_client,
        APIError("Unknown API error code: 1097"),
        conversation_id="stale-conversation",
    )
    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(_request("stale-conversation"))
    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == (
        "The provided conversation_id can no longer be recovered. Start a new conversation."
    )


@pytest.mark.asyncio
async def test_stateful_buffered_1097_without_410_rule_maps_502(
    mocker, provider, install_gemini_client
):
    # New conversation: the 410 rule does not apply, shared mapping wins (not 500).
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash", is_available=True
    )
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)
    mock_manager.get_response_stateful = mocker.AsyncMock(
        side_effect=APIError("Unknown API error code: 1097")
    )
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()
    install_gemini_client(mock_client)
    mocker.patch(
        "app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry",
        return_value=mock_registry,
    )
    mocker.patch(
        "app.services.providers.gemini.provider.generate_opaque_token",
        return_value="new-conversation",
    )
    request = OpenAIChatRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="gemini-3-flash",
        stream=False,
    )
    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(request)
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Gemini WebAPI provider request failed."


@pytest.mark.asyncio
async def test_stateful_buffered_unexpected_error_still_500(
    mocker, provider, install_gemini_client
):
    await _assert_buffered_status(
        mocker, provider, install_gemini_client, RuntimeError("boom"), 500
    )


@pytest.mark.parametrize(
    ("failure", "status", "detail"),
    [
        (GeminiProviderOutputError("x"), 502, "Gemini WebAPI returned malformed tool output."),
        (AuthError("x"), 503, "Gemini WebAPI authentication is unavailable."),
        (asyncio.TimeoutError(), 504, "Gemini WebAPI request timed out."),
        (GeminiTimeoutError("x"), 504, "Gemini WebAPI request timed out."),
        (UsageLimitExceededError("x"), 429, "Gemini WebAPI usage limit exceeded."),
        (TemporarilyBlockedError("x"), 429, "Gemini WebAPI request is temporarily blocked."),
        (ModelInvalidError("x"), 502, "Gemini WebAPI rejected the requested model."),
        (APIError("x"), 502, "Gemini WebAPI provider request failed."),
        (GeminiError("x"), 502, "Gemini WebAPI provider request failed."),
    ],
)
def test_shared_translator_preserves_stateless_mappings(failure, status, detail):
    for translate in (translate_gemini_provider_error, stateless_chat._translate_direct_gemini_error):
        translated = translate(failure)
        assert isinstance(translated, HTTPException)
        assert translated.status_code == status
        assert translated.detail == detail


def test_shared_translator_returns_none_for_unexpected():
    assert translate_gemini_provider_error(RuntimeError("boom")) is None
    assert stateless_chat._translate_direct_gemini_error(RuntimeError("boom")) is None


@pytest.mark.asyncio
async def test_stateful_buffered_api_error_logged_once_at_adapter_boundary(
    mocker, provider, install_gemini_client, caplog
):
    """End-to-end buffered failure: 502 + exactly one adapter error log, none from SessionManager."""
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash", is_available=True
    )
    generation = install_gemini_client(mock_client)
    manager = SessionManager(mock_client, generation)
    manager.session = SimpleNamespace(
        send_message=AsyncMock(side_effect=APIError("provider error"))
    )
    manager.model = "gemini-3-flash"
    manager.gem = None
    manager.session_generation = generation
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_registry.get_session = mocker.AsyncMock(return_value=manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()
    mocker.patch(
        "app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry",
        return_value=mock_registry,
    )

    with caplog.at_level(logging.ERROR, logger="app"):
        with pytest.raises(HTTPException) as exc_info:
            await provider.chat_completions(_request())

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Gemini WebAPI provider request failed."
    assert "provider error" not in exc_info.value.detail

    error_records = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR and r.name.startswith("app")
    ]
    assert len(error_records) == 1
    assert "GeminiWebAPIAdapter.chat_completions" in error_records[0].message


@pytest.mark.asyncio
async def test_buffered_session_manager_does_not_log_before_reraising(caplog):
    client = SimpleNamespace()
    manager = SessionManager(client, 0)
    manager.session = SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("boom"))
    )
    manager.model = "m"
    manager.gem = None
    manager.session_generation = 0
    lease = SimpleNamespace(client=client, generation=0, assert_active=Mock(), release=AsyncMock())

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="boom"):
            await manager.get_response_stateful(
                "m", [{"role": "user", "content": "hi"}], "", lease=lease
            )

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR and r.name.startswith("app")]
    lease.release.assert_not_called()
