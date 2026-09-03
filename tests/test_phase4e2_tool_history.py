from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.request import OpenAIChatRequest
from app.services.multimodal import normalize_openai_chat_messages
from app.services.providers.gemini.session_manager import transform_messages
from app.services.providers.gemini.shared import validate_tool_history


def _call(call_id: str, name: str = "lookup", arguments: str = '{"city":"London"}') -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _tool(call_id: str, content="result") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


async def _post(payload: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/stateless/chat/completions", json=payload)


def _available_client(mocker):
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"
    client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash",
        is_available=True,
    )
    client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text="final"))
    return client


INVALID_HISTORIES = [
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("")]}],
        id="empty-assistant-call-id",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{**_call("call_a"), "id": "   "}]}],
        id="whitespace-assistant-call-id",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a"), _call("call_a", "other")]}],
        id="duplicate-call-id-within-group",
    ),
    pytest.param(
        [
            {"role": "assistant", "tool_calls": [_call("call_a")]},
            _tool("call_a"),
            {"role": "assistant", "tool_calls": [_call("call_a", "other")]},
            _tool("call_a"),
        ],
        id="duplicate-call-id-across-history",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function", "function": None}]}],
        id="null-function",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{**_call("call_a"), "type": "other"}]}],
        id="unsupported-call-type",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function"}]}],
        id="missing-function",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function", "function": {"arguments": "{}"}}]}],
        id="missing-function-name",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a", name="")]}],
        id="empty-function-name",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "lookup"}}]}],
        id="missing-arguments",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "lookup", "arguments": {}}}]}],
        id="object-arguments",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "lookup", "arguments": None}}]}],
        id="null-arguments",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "lookup", "arguments": 1}}]}],
        id="number-arguments",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "lookup", "arguments": True}}]}],
        id="boolean-arguments",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a", arguments="not-json")]}],
        id="malformed-arguments-json",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a", arguments="[]")]}],
        id="array-arguments-json",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a", arguments="1")]}],
        id="scalar-arguments-json",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a", arguments="null")]}],
        id="null-root-arguments-json",
    ),
    pytest.param([_tool("call_a")], id="orphan-result"),
    pytest.param(
        [_tool("call_a"), {"role": "assistant", "tool_calls": [_call("call_a")] }],
        id="result-before-call",
    ),
    pytest.param([_tool("call_unknown")], id="unknown-result-id"),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a")]}, _tool("call_a"), _tool("call_a")],
        id="duplicate-result",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a")]}, {"role": "user", "content": "continue"}],
        id="unresolved-before-user",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a")]}, {"role": "assistant", "content": "continue"}],
        id="unresolved-before-assistant",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a")]}, {"role": "system", "content": "continue"}],
        id="unresolved-before-system",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a")]}, {"role": "assistant", "tool_calls": [_call("call_b")] }],
        id="consecutive-assistant-tool-groups",
    ),
    pytest.param(
        [{"role": "assistant", "tool_calls": []}],
        id="empty-tool-call-list",
    ),
    pytest.param([_tool("", "result")], id="empty-result-id"),
    pytest.param([{"role": "tool", "content": "result"}], id="missing-result-id"),
    pytest.param(
        [{"role": "assistant", "tool_calls": [_call("call_a")] }],
        id="unresolved-at-end",
    ),
]


def test_transform_messages_preserves_historical_call_ids_and_order():
    messages = [
        {"role": "assistant", "tool_calls": [_call("call_a", arguments='{ "city": "London" }'), _call("call_b")]},
        _tool("call_b", "B"),
        _tool("call_a", "A"),
    ]

    assert transform_messages(messages) == [
        'Assistant tool call [call_a] lookup: { "city": "London" }',
        'Assistant tool call [call_b] lookup: {"city":"London"}',
        "Tool result [call_b]: B",
        "Tool result [call_a]: A",
    ]


def test_validator_allows_sequential_tool_call_groups_and_reverse_results():
    validate_tool_history(
        [
            {"role": "assistant", "tool_calls": [_call("call_a")]},
            _tool("call_a", "A"),
            {"role": "assistant", "tool_calls": [_call("call_b", "other")]},
            _tool("call_b", "B"),
            {"role": "assistant", "tool_calls": [_call("call_c"), _call("call_d")]},
            _tool("call_d", "D"),
            _tool("call_c", "C"),
            {"role": "user", "content": "continue"},
        ]
    )


@pytest.mark.parametrize("content", ["", None])
def test_validator_accepts_empty_or_null_tool_content(content):
    validate_tool_history(
        [
            {"role": "assistant", "tool_calls": [_call("call_a")]},
            _tool("call_a", content),
        ]
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [("", ""), (None, ""), ([{"type": "text", "text": "result"}], "result")],
)
def test_tool_content_is_normalized_without_changing_association(content, expected):
    request = OpenAIChatRequest(
        messages=[
            {"role": "assistant", "tool_calls": [_call("call_a")]},
            _tool("call_a", content),
        ]
    )

    normalized = normalize_openai_chat_messages(request.messages, allow_file_parts=True)
    validate_tool_history(normalized.messages)

    assert transform_messages(normalized.messages)[-1] == f"Tool result [call_a]: {expected}"


@pytest.mark.asyncio
async def test_stateless_tool_loop_preserves_ids_and_uses_temporary_execution(
    mocker,
    install_gemini_client,
):
    client = _available_client(mocker)
    install_gemini_client(client)
    mocker.patch(
        "app.services.providers.gemini.session_manager.SessionRegistry.get_session",
        side_effect=AssertionError("stateless history must not restore sessions"),
    )
    mocker.patch(
        "app.services.providers.gemini.session_manager.SessionRegistry.save_session_snapshot",
        side_effect=AssertionError("stateless history must not save snapshots"),
    )

    response = await _post(
        {
            "model": "gemini-3-flash",
            "messages": [
                {"role": "user", "content": "Weather?"},
                {"role": "assistant", "tool_calls": [_call("call_a", "get_weather")]},
                _tool("call_a", "18 C"),
                {"role": "user", "content": "Summarize."},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "different_current_tool", "parameters": {"type": "object"}},
                }
            ],
        }
    )

    assert response.status_code == 200
    prompt = client.generate_content.await_args.args[0]
    assert "Assistant tool call [call_a] get_weather: {\"city\":\"London\"}" in prompt
    assert "Tool result [call_a]: 18 C" in prompt
    assert "User: Summarize." in prompt
    assert client.generate_content.await_args.kwargs["temporary"] is True


@pytest.mark.asyncio
async def test_stateless_repeated_tool_loop_keeps_history_independent(
    mocker,
    install_gemini_client,
):
    client = _available_client(mocker)
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "messages": [
                {"role": "user", "content": "First"},
                {"role": "assistant", "tool_calls": [_call("call_a", "old_tool_a")]},
                _tool("call_a", "A"),
                {"role": "user", "content": "Second"},
                {"role": "assistant", "tool_calls": [_call("call_b", "old_tool_b")]},
                _tool("call_b", "B"),
                {"role": "user", "content": "Final"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "current_tool", "parameters": {"type": "object"}},
                }
            ],
        }
    )

    assert response.status_code == 200
    prompt = client.generate_content.await_args.args[0]
    assert prompt.index("Assistant tool call [call_a]") < prompt.index("Tool result [call_a]: A")
    assert prompt.index("Tool result [call_a]: A") < prompt.index("Assistant tool call [call_b]")
    assert prompt.index("Assistant tool call [call_b]") < prompt.index("Tool result [call_b]: B")
    assert prompt.endswith("User: Final")
    assert client.generate_content.await_args.kwargs["temporary"] is True


@pytest.mark.asyncio
async def test_stateless_multiple_calls_allow_results_in_reverse_order(mocker, install_gemini_client):
    client = _available_client(mocker)
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "messages": [
                {"role": "assistant", "tool_calls": [_call("call_a", "a"), _call("call_b", "b")]},
                _tool("call_b", "B"),
                _tool("call_a", "A"),
                {"role": "user", "content": "Continue"},
            ],
        }
    )

    assert response.status_code == 200
    prompt = client.generate_content.await_args.args[0]
    assert "Assistant tool call [call_a] a:" in prompt
    assert "Assistant tool call [call_b] b:" in prompt
    assert prompt.index("Tool result [call_b]: B") < prompt.index("Tool result [call_a]: A")
    assert client.generate_content.await_args.kwargs["temporary"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("messages", INVALID_HISTORIES)
async def test_invalid_tool_history_returns_422_not_provider_error(
    mocker,
    install_gemini_client,
    messages,
):
    client = _available_client(mocker)
    install_gemini_client(client)

    response = await _post({"model": "gemini-3-flash", "messages": messages})

    assert response.status_code == 422
    assert response.json()["detail"].startswith("Invalid tool history:")
    client.generate_content.assert_not_awaited()
