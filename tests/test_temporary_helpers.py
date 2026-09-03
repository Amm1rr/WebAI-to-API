"""Regression for temporary compatibility helper wrappers.

They must preserve legacy behavior: endpoint_name="temporary",
direct_webapi_only=False, including gemini/<model> normalization.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.schemas.request import OpenAIChatRequest
from app.services.providers.gemini.stateless_chat import (
    _prepare_stateless_chat_request,
    _resolve_stateless_chat_model,
    _validate_stateless_chat_request,
)
from app.services.providers.gemini.temporary_chat import (
    TemporaryChatRequestContext,
    _prepare_temporary_chat_request,
    _resolve_temporary_chat_model,
    _validate_temporary_chat_request,
)
from app.services.providers.gemini.stateless_chat import StatelessChatRequestContext


def _available_mock(mocker, available_models: set[str] | None = None):
    available_models = available_models or {"gemini-3-flash"}
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"

    def _resolve(name: str):
        if name in available_models:
            return SimpleNamespace(model_name=name, is_available=True)
        raise ValueError(f"Unknown model name: {name}")

    client.resolve_model.side_effect = _resolve
    return client


def _request(model: str = "gemini-3-flash", **kwargs) -> OpenAIChatRequest:
    return OpenAIChatRequest(
        model=model,
        messages=[{"role": "user", "content": "Hello"}],
        **kwargs,
    )


def test_temporary_helpers_are_wrappers_not_aliases():
    # Preserve alias for context, but helpers must be wrappers
    assert TemporaryChatRequestContext is StatelessChatRequestContext
    assert _validate_temporary_chat_request is not _validate_stateless_chat_request
    assert _resolve_temporary_chat_model is not _resolve_stateless_chat_model
    assert _prepare_temporary_chat_request is not _prepare_stateless_chat_request


def test_resolve_temporary_normalizes_gemini_prefix(mocker):
    client = _available_mock(mocker, {"gemini-3-flash"})
    req = _request(model="gemini/gemini-3-flash")
    result = _resolve_temporary_chat_model(req, client)
    assert result == "gemini-3-flash"
    client.resolve_model.assert_called_once_with("gemini-3-flash")


def test_prepare_temporary_normalizes_gemini_prefix(mocker):
    client = _available_mock(mocker, {"gemini-3-flash"})
    req = _request(model="gemini/gemini-3-flash")
    ctx = _prepare_temporary_chat_request(req, client)
    assert ctx.model == "gemini-3-flash"
    # ensure resolved via normalized name
    client.resolve_model.assert_called_once_with("gemini-3-flash")


def test_temporary_error_messages_refer_to_temporary(mocker):
    client = _available_mock(mocker)
    # conversation_id error
    with pytest.raises(HTTPException) as exc:
        _validate_temporary_chat_request(_request(model="gemini-3-flash", conversation_id="abc"))
    assert exc.value.status_code == 400
    assert "temporary chat endpoint" in exc.value.detail
    assert "stateless chat endpoint" not in exc.value.detail

    # also via resolve wrapper
    with pytest.raises(HTTPException) as exc2:
        _resolve_temporary_chat_model(_request(model="gemini-3-flash", conversation_id="abc"), client)
    assert "temporary chat endpoint" in exc2.value.detail

    # and via prepare wrapper
    with pytest.raises(HTTPException) as exc3:
        _prepare_temporary_chat_request(_request(model="gemini-3-flash", conversation_id="abc"), client)
    assert "temporary chat endpoint" in exc3.value.detail


def test_stateless_helpers_remain_strict(mocker):
    client = _available_mock(mocker, {"gemini-3-flash"})
    # Stateless must NOT normalize gemini/ prefix — it stays as slash id
    req = _request(model="gemini/gemini-3-flash")
    model = _validate_stateless_chat_request(req)
    assert model == "gemini/gemini-3-flash"

    # Resolve should fail because catalog doesn't have slash variant
    with pytest.raises(HTTPException) as exc2:
        _resolve_stateless_chat_model(req, client)
    assert "Unknown model name" in exc2.value.detail or "not available" in exc2.value.detail.lower()
    # ensure it was called with slash model, not normalized
    client.resolve_model.assert_called_with("gemini/gemini-3-flash")

    # Error messages say stateless
    with pytest.raises(HTTPException) as exc3:
        _validate_stateless_chat_request(_request(model="gemini-3-flash", conversation_id="abc"))
    assert "stateless chat endpoint" in exc3.value.detail
    assert "temporary chat endpoint" not in exc3.value.detail

    # Prepare also strict (fails via resolver)
    with pytest.raises(HTTPException):
        _prepare_stateless_chat_request(req, client)
