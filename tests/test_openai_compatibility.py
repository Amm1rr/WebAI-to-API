from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.main import app
from app.schemas.request import OpenAIChatRequest, OpenAIToolChoiceFunctionSelection
from app.services.openai_compatibility import (
    OpenAIRequestCapability,
    validate_openai_request_compatibility,
)
from app.services.providers.atlas.provider import AtlasProvider
from app.services.providers.atlas.client import AtlasClient
from app.services.providers.gemini.base_adapter import (
    GEMINI_PLAYWRIGHT_OPENAI_COMPATIBILITY,
    GEMINI_WEBAPI_OPENAI_COMPATIBILITY,
)
from app.services.providers.gemini.provider import GeminiProvider


def _messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "Hello"}]


def _request(**kwargs) -> OpenAIChatRequest:
    return OpenAIChatRequest(
        messages=_messages(),
        model=kwargs.pop("model", "gemini-3-flash"),
        **kwargs,
    )


def test_openai_request_declares_audited_controls_and_preserves_tolerant_extras():
    request = OpenAIChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "Hello", "future_message_field": True}],
            "max_tokens": 12,
            "temperature": 0,
            "top_p": 0.5,
            "top_k": 4,
            "reasoning_effort": "high",
            "stream_options": {"include_usage": True},
            "response_format": {"type": "json_object"},
            "parallel_tool_calls": False,
            "future_request_field": "ignored",
        }
    )

    assert request.max_tokens == 12
    assert request.temperature == 0
    assert request.top_p == 0.5
    assert request.top_k == 4
    assert request.reasoning_effort == "high"
    assert request.stream_options.include_usage is True
    assert request.response_format.type == "json_object"
    assert request.messages[0].future_message_field is True
    assert not hasattr(request, "future_request_field")


@pytest.mark.parametrize(
    "tools",
    [
        ["not-an-object"],
        [{"type": "function", "function": {}}],
        [{"type": "function", "function": {"name": ""}}],
        [{"type": "function", "function": {"name": 123}}],
        [{"type": "function"}],
        [{"type": "function", "function": None}],
        [{"type": "other", "function": {"name": "lookup"}}],
    ],
)
def test_openai_request_rejects_malformed_tool_declarations(tools):
    with pytest.raises(ValidationError, match="Invalid tool declaration"):
        _request(tools=tools)


@pytest.mark.parametrize(
    "tool_choice",
    [
        "none",
        "auto",
        "required",
        {"type": "function", "function": {"name": "lookup"}},
    ],
)
def test_tool_choice_accepts_only_contract_shapes(tool_choice):
    request = _request(tool_choice=tool_choice)

    if isinstance(tool_choice, str):
        assert request.tool_choice == tool_choice
    else:
        assert isinstance(request.tool_choice, OpenAIToolChoiceFunctionSelection)
        assert request.tool_choice.model_dump(mode="json") == tool_choice


@pytest.mark.parametrize(
    "payload",
    [
        {"max_tokens": 0},
        {"max_tokens": True},
        {"max_completion_tokens": -1},
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"top_p": -0.1},
        {"top_p": 1.1},
        {"top_k": 0},
        {"reasoning_effort": "unsupported"},
        {"stream_options": {"unknown": True}},
        {"response_format": {"type": "json_schema"}},
        {"response_format": {"type": "json_object", "json_schema": {"name": "x", "schema": {}}}},
        {"tool_choice": 123},
        {"tool_choice": True},
        {"tool_choice": ["auto"]},
        {"tool_choice": "invalid-mode"},
        {"tool_choice": {"type": "function"}},
        {"tool_choice": {"type": "function", "function": {"name": ""}}},
        {"tool_choice": {"type": "other", "function": {"name": "lookup"}}},
        {
            "tool_choice": {
                "type": "function",
                "function": {"name": "lookup", "extra": True},
            }
        },
    ],
)
def test_invalid_audited_controls_fail_schema_validation(payload):
    with pytest.raises(ValidationError):
        _request(**payload)


def test_token_aliases_cannot_be_supplied_together():
    with pytest.raises(ValidationError, match="mutually exclusive"):
        _request(max_tokens=12, max_completion_tokens=12)


def test_response_format_json_schema_uses_openai_schema_key():
    request = _request(
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "schema": {"type": "object"},
            },
        }
    )

    assert request.response_format.json_schema.schema_ == {"type": "object"}
    assert request.model_dump(by_alias=True)["response_format"]["json_schema"]["schema"] == {
        "type": "object"
    }


def test_backend_capabilities_are_explicit():
    assert (
        GEMINI_WEBAPI_OPENAI_COMPATIBILITY.status("max_tokens")
        is OpenAIRequestCapability.ACCEPTED_NO_EFFECT
    )
    assert (
        GEMINI_PLAYWRIGHT_OPENAI_COMPATIBILITY.status("reasoning_effort")
        is OpenAIRequestCapability.ACCEPTED_NO_EFFECT
    )
    assert (
        GEMINI_WEBAPI_OPENAI_COMPATIBILITY.status("temperature")
        is OpenAIRequestCapability.UNSUPPORTED
    )
    assert (
        AtlasProvider.openai_compatibility.status("tool_choice")
        is OpenAIRequestCapability.SUPPORTED
    )
    assert (
        AtlasProvider.openai_compatibility.status("max_tokens")
        is OpenAIRequestCapability.UNSUPPORTED
    )

    provider = GeminiProvider()
    assert (
        provider.get_openai_compatibility_capabilities(
            _request(model="playwright/gemini-3.5-flash")
        )
        is GEMINI_PLAYWRIGHT_OPENAI_COMPATIBILITY
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("temperature", 0),
        ("top_p", 0.5),
        ("top_k", 1),
        ("response_format", {"type": "json_object"}),
        ("parallel_tool_calls", False),
        ("tool_choice", "auto"),
        ("tool_choice", {"type": "function", "function": {"name": "lookup"}}),
    ],
)
def test_gemini_capability_validator_rejects_explicit_unsupported_fields(field_name, value):
    with pytest.raises(HTTPException) as error:
        validate_openai_request_compatibility(
            _request(**{field_name: value}),
            GEMINI_WEBAPI_OPENAI_COMPATIBILITY,
        )

    assert error.value.status_code == 400
    assert (
        error.value.detail
        == f"Unsupported parameter: {field_name} (code: unsupported_parameter)."
    )


def test_gemini_capability_validator_reports_one_unsupported_field_in_order():
    with pytest.raises(HTTPException) as first_error:
        validate_openai_request_compatibility(
            _request(
                temperature=0.2,
                response_format={"type": "json_object"},
            ),
            GEMINI_WEBAPI_OPENAI_COMPATIBILITY,
        )

    assert first_error.value.status_code == 400
    assert (
        first_error.value.detail
        == "Unsupported parameter: temperature (code: unsupported_parameter)."
    )

    with pytest.raises(HTTPException) as second_error:
        validate_openai_request_compatibility(
            _request(response_format={"type": "json_object"}),
            GEMINI_WEBAPI_OPENAI_COMPATIBILITY,
        )

    assert second_error.value.status_code == 400
    assert (
        second_error.value.detail
        == "Unsupported parameter: response_format (code: unsupported_parameter)."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/stateless/chat/completions", "/v1/temporary/chat/completions"])
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("temperature", 0),
        ("top_p", 0.5),
        ("top_k", 1),
        ("response_format", {"type": "json_object"}),
        ("parallel_tool_calls", False),
        ("tool_choice", "auto"),
        ("tool_choice", {"type": "function", "function": {"name": "lookup"}}),
    ],
)
async def test_gemini_temporary_routes_reject_unsupported_controls_before_lease(
    mocker,
    path,
    field_name,
    value,
):
    acquire_lease = mocker.patch(
        "app.services.providers.gemini.stateless_chat.acquire_current_gemini_lease"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            path,
            json={
                "model": "gemini-3-flash",
                "messages": _messages(),
                field_name: value,
            },
        )

    assert response.status_code == 400
    assert field_name in response.json()["detail"]
    acquire_lease.assert_not_called()


@pytest.mark.asyncio
async def test_primary_route_rejects_unsupported_controls_before_provider_execution(mocker):
    provider = GeminiProvider()
    provider.chat_completions = AsyncMock()
    mocker.patch(
        "app.endpoints.chat.ProviderFactory.get_provider",
        return_value=(provider, "gemini-3-flash"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": _messages(),
                "temperature": 0,
            },
        )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "Unsupported parameter: temperature (code: unsupported_parameter)."
    )
    provider.chat_completions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/stateless/chat/completions", "/v1/temporary/chat/completions"])
async def test_gemini_routes_report_unsupported_parameters_one_at_a_time(mocker, path):
    acquire_lease = mocker.patch(
        "app.services.providers.gemini.stateless_chat.acquire_current_gemini_lease"
    )
    payload = {
        "model": "gemini-3-flash",
        "messages": _messages(),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, json=payload)
        retry_response = await client.post(
            path,
            json={key: value for key, value in payload.items() if key != "temperature"},
        )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "Unsupported parameter: temperature (code: unsupported_parameter)."
    )
    assert retry_response.status_code == 400
    assert (
        retry_response.json()["detail"]
        == "Unsupported parameter: response_format (code: unsupported_parameter)."
    )
    acquire_lease.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gemini-3-flash", "playwright/gemini-3.5-flash"])
@pytest.mark.parametrize(
    "tool_choice",
    [
        "auto",
        {"type": "function", "function": {"name": "lookup"}},
    ],
)
async def test_primary_gemini_routes_reject_valid_tool_choice_before_execution(
    mocker,
    model,
    tool_choice,
):
    provider = GeminiProvider()
    provider.chat_completions = AsyncMock()
    mocker.patch(
        "app.endpoints.chat.ProviderFactory.get_provider",
        return_value=(provider, model),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": _messages(),
                "tool_choice": tool_choice,
            },
        )

    assert response.status_code == 400
    assert "tool_choice" in response.json()["detail"]
    provider.chat_completions.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_schema_errors_return_422_before_provider_resolution(mocker):
    get_provider = mocker.patch("app.endpoints.chat.ProviderFactory.get_provider")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": _messages(),
                "max_tokens": 100,
                "max_completion_tokens": 100,
            },
        )

    assert response.status_code == 422
    get_provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_choice",
    [
        123,
        True,
        "invalid-mode",
        {"type": "function"},
        {"type": "function", "function": {"name": ""}},
        {"type": "other", "function": {"name": "lookup"}},
    ],
)
async def test_http_malformed_tool_choice_returns_422_before_provider_resolution(mocker, tool_choice):
    get_provider = mocker.patch("app.endpoints.chat.ProviderFactory.get_provider")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": _messages(),
                "tool_choice": tool_choice,
            },
        )

    assert response.status_code == 422
    get_provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/stateless/chat/completions", "/v1/temporary/chat/completions"])
async def test_gemini_routes_reject_malformed_tool_declarations_before_lease(mocker, path):
    acquire_lease = mocker.patch(
        "app.services.providers.gemini.stateless_chat.acquire_current_gemini_lease"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            path,
            json={
                "model": "gemini-3-flash",
                "messages": _messages(),
                "tools": [{"type": "function", "function": {}}],
            },
        )

    assert response.status_code == 422
    assert "function.name" in response.json()["detail"][0]["msg"]
    acquire_lease.assert_not_called()


@pytest.mark.asyncio
async def test_primary_gemini_rejects_malformed_tool_declaration_before_provider_resolution(mocker):
    get_provider = mocker.patch("app.endpoints.chat.ProviderFactory.get_provider")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": _messages(),
                "tools": [{"type": "function", "function": {"name": 123}}],
            },
        )

    assert response.status_code == 422
    assert "function.name" in response.json()["detail"][0]["msg"]
    get_provider.assert_not_called()


@pytest.mark.asyncio
async def test_atlas_route_rejects_unforwarded_controls_before_provider_execution(mocker):
    provider = AtlasProvider()
    provider.chat_completions = AsyncMock()
    mocker.patch(
        "app.endpoints.chat.ProviderFactory.get_provider",
        return_value=(provider, "MiniMax-M2"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "atlas/MiniMax-M2",
                "messages": _messages(),
                "max_tokens": 100,
            },
        )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "Unsupported parameter: max_tokens (code: unsupported_parameter)."
    )
    provider.chat_completions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/stateless/chat/completions", "/v1/temporary/chat/completions"])
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_tokens", 100),
        ("max_completion_tokens", 100),
        ("reasoning_effort", "high"),
        ("stream_options", {"include_usage": True}),
    ],
)
async def test_gemini_compatibility_noops_are_not_forwarded(
    mocker,
    install_gemini_client,
    path,
    field_name,
    value,
):
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"
    client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash",
        is_available=True,
    )
    client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text="ok"))
    install_gemini_client(client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        response = await http_client.post(
            path,
            json={
                "model": "gemini-3-flash",
                "messages": _messages(),
                field_name: value,
            },
        )

    assert response.status_code == 200
    client.generate_content.assert_awaited_once_with(
        "User: Hello",
        "gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_choice",
    [
        "auto",
        {"type": "function", "function": {"name": "lookup"}},
    ],
)
async def test_atlas_forwards_tool_choice_without_modification(mocker, tool_choice):
    response = MagicMock()
    response.json.return_value = {"choices": []}
    response.aclose = AsyncMock()
    response._atlas_client = MagicMock()
    response._atlas_client.aclose = AsyncMock()

    atlas_client = MagicMock()
    atlas_client.chat_completions = AsyncMock(return_value=response)
    mocker.patch(
        "app.services.providers.atlas.provider.get_atlas_client",
        return_value=atlas_client,
    )

    tools = [{"type": "function", "function": {"name": "lookup"}}]
    request = OpenAIChatRequest(
        model="MiniMax-M2",
        messages=_messages(),
        tools=tools,
        tool_choice=tool_choice,
    )
    expected_tool_choice = (
        OpenAIToolChoiceFunctionSelection.model_validate(tool_choice)
        if isinstance(tool_choice, dict)
        else tool_choice
    )

    await AtlasProvider().chat_completions(request)

    atlas_client.chat_completions.assert_awaited_once_with(
        messages=_messages(),
        model="MiniMax-M2",
        stream=False,
        tools=tools,
        tool_choice=expected_tool_choice,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_choice",
    [
        "auto",
        {"type": "function", "function": {"name": "lookup"}},
    ],
)
async def test_atlas_client_serializes_tool_choice_as_json(mocker, tool_choice):
    http_client = mocker.patch("app.services.providers.atlas.client.httpx.AsyncClient").return_value
    response = MagicMock()
    response.is_error = False
    http_client.send = AsyncMock(return_value=response)
    typed_tool_choice = (
        OpenAIToolChoiceFunctionSelection.model_validate(tool_choice)
        if isinstance(tool_choice, dict)
        else tool_choice
    )

    await AtlasClient(api_key="test", base_url="https://api.atlascloud.ai/v1").chat_completions(
        messages=_messages(),
        model="MiniMax-M2",
        tool_choice=typed_tool_choice,
    )

    http_client.build_request.assert_called_once_with(
        "POST",
        "chat/completions",
        json={
            "model": "MiniMax-M2",
            "messages": _messages(),
            "stream": False,
            "tool_choice": tool_choice,
        },
    )


@pytest.mark.asyncio
async def test_openapi_exposes_audited_controls_and_400_contract():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    request_schema = schema["components"]["schemas"]["OpenAIChatRequest"]
    for field_name in (
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "top_k",
        "reasoning_effort",
        "stream_options",
        "response_format",
        "parallel_tool_calls",
        "tool_choice",
    ):
        assert field_name in request_schema["properties"]

    tool_choice_schema = request_schema["properties"]["tool_choice"]
    assert any(
        item.get("enum") == ["none", "auto", "required"]
        for item in tool_choice_schema["anyOf"]
    )
    assert any(
        item.get("$ref", "").endswith("/OpenAIToolChoiceFunctionSelection")
        for item in tool_choice_schema["anyOf"]
    )

    chat_responses = schema["paths"]["/v1/chat/completions"]["post"]["responses"]
    assert "400" in chat_responses
    unsupported_example = chat_responses["400"]["content"]["application/json"]["examples"][
        "unsupportedRequestFields"
    ]
    assert (
        unsupported_example["value"]["detail"]
        == "Unsupported parameter: temperature (code: unsupported_parameter)."
    )
