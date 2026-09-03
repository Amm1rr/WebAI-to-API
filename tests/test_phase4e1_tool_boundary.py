import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.providers.exceptions import GeminiProviderOutputError
from app.services.providers.gemini.shared import (
    ToolCallParseStatus,
    build_tools_prompt,
    convert_to_openai_format,
    parse_tool_call,
)
from app.utils.streaming import (
    convert_chat_completion_to_streaming_chunk,
    simulate_streaming_generator,
)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "parameters": {"type": "object"},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "parameters": {"type": "object"},
        },
    },
]


def _parse(text: str):
    return parse_tool_call(text, tools=TOOLS)


def _available_client(mocker, response_text: str):
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"
    client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash",
        is_available=True,
    )
    client.generate_content = mocker.AsyncMock(
        return_value=SimpleNamespace(text=response_text),
    )
    return client


def test_build_tools_prompt_rejects_malformed_declaration_without_keyerror():
    with pytest.raises(HTTPException) as error:
        build_tools_prompt([{"type": "function", "function": {}}])

    assert error.value.status_code == 422
    assert error.value.detail == "Invalid tool declaration: function.name must be a non-empty string."


async def _post(payload: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/stateless/chat/completions", json=payload)


def test_parse_tool_call_accepts_exact_object_and_fence():
    object_result = _parse(
        '  {"tool_call":{"name":"get_weather","arguments":{"city":"London"}}}  '
    )
    json_fence_result = _parse(
        '```json\n{"tool_call":{"name":"get_weather","arguments":{}}}\n```'
    )
    plain_fence_result = _parse(
        '```\n{"tool_call":{"name":"search_web","arguments":{}}}\n```'
    )

    assert object_result.status is ToolCallParseStatus.VALID_TOOL_CALL
    assert object_result.tool_call["arguments"] == {"city": "London"}
    assert json_fence_result.status is ToolCallParseStatus.VALID_TOOL_CALL
    assert json_fence_result.tool_call["arguments"] == {}
    assert plain_fence_result.status is ToolCallParseStatus.VALID_TOOL_CALL
    assert plain_fence_result.tool_call["name"] == "search_web"


@pytest.mark.parametrize(
    "text",
    [
        "The weather is sunny.",
        'Here is an example:\n{"tool_call":{"name":"get_weather","arguments":{"city":"London"}}}',
        '{"tool_call":{"name":"get_weather","arguments":{"city":"London"}}}\nThat is all.',
        '{"other":"value"}',
        "{}",
        "```json\n{broken}\n```",
        "```\n{broken}\n```",
        '[{"city":"London"}]',
        "[1, 2, 3]",
    ],
)
def test_parse_tool_call_keeps_non_exact_output_as_plain_text(text):
    assert _parse(text).status is ToolCallParseStatus.NO_TOOL_CALL


@pytest.mark.parametrize(
    "text",
    [
        '{"tool_call":"foo"}',
        '{"tool_call":{}}',
        '{"tool_call":{"name":123,"arguments":{}}}',
        '{"tool_call":{"name":"foo","arguments":null}}',
        '{"tool_call":{"name":"foo"}}',
        '{"tool_call":{"name":"","arguments":{}}}',
        '{"tool_call":{"name":"foo","arguments":"{}"}}',
        '{"tool_call":{"name":"foo","arguments":[]}}',
        '{"tool_call":{"name":"foo","arguments":1}}',
        '{"tool_call":{"name":"foo","arguments":true}}',
        '{"tool_call":{"name":"delete_database","arguments":{}}}',
        "```json\n{\"tool_call\":\n```",
        "```\n{\"tool_calls\":\n```",
        '[{"tool_call":{"name":"get_weather","arguments":{}}}]',
        '{"tool_calls":[{"name":"get_weather","arguments":{}}]}',
    ],
)
def test_parse_tool_call_rejects_malformed_or_unsupported_output(text):
    result = _parse(text)

    assert result.status is ToolCallParseStatus.INVALID_TOOL_CALL
    assert result.tool_call is None


def test_convert_tool_call_serializes_arguments_and_generates_unique_ids(mocker):
    mocker.patch("app.services.providers.gemini.shared.time.time", return_value=123)
    tool_call = {"name": "get_weather", "arguments": {"location": {"city": "London"}}}

    first = convert_to_openai_format("", "gemini-3-flash", tool_call=tool_call)
    second = convert_to_openai_format("", "gemini-3-flash", tool_call=tool_call)

    first_call = first["choices"][0]["message"]["tool_calls"][0]
    second_call = second["choices"][0]["message"]["tool_calls"][0]
    assert first["choices"][0]["finish_reason"] == "tool_calls"
    assert first["choices"][0]["message"]["content"] is None
    assert first_call["function"]["name"] == "get_weather"
    assert isinstance(first_call["function"]["arguments"], str)
    assert json.loads(first_call["function"]["arguments"]) == tool_call["arguments"]
    assert first["id"] != second["id"]
    assert first_call["id"] != second_call["id"]


@pytest.mark.asyncio
async def test_streaming_replay_preserves_valid_tool_call_and_id():
    buffered = convert_to_openai_format(
        "",
        "gemini-3-flash",
        tool_call={"name": "get_weather", "arguments": {"city": "London"}},
    )
    chunk = convert_chat_completion_to_streaming_chunk(buffered)

    buffered_call = buffered["choices"][0]["message"]["tool_calls"][0]
    replay_choice = chunk["choices"][0]
    replay_call = replay_choice["delta"]["tool_calls"][0]
    assert "message" not in replay_choice
    assert replay_choice["finish_reason"] == "tool_calls"
    assert replay_call["index"] == 0
    assert replay_call["id"] == buffered_call["id"]
    assert replay_call["function"]["arguments"] == buffered_call["function"]["arguments"]

    events = [event async for event in simulate_streaming_generator(chunk)]
    assert events[-1] == "data: [DONE]\n\n"


def test_streaming_replay_rejects_malformed_buffered_tool_calls():
    completion = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1"}, {"id": "call_2"}],
            }
        }],
    }

    with pytest.raises(GeminiProviderOutputError):
        convert_chat_completion_to_streaming_chunk(completion)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_malformed_tool_output_maps_to_502_before_response(mocker, install_gemini_client, stream):
    client = _available_client(mocker, '{"tool_call":{"name":"get_weather","arguments":null}}')
    install_gemini_client(client)

    response = await _post({
        "model": "gemini-3-flash",
        "stream": stream,
        "messages": [{"role": "user", "content": "Weather?"}],
        "tools": [TOOLS[0]],
    })

    assert response.status_code == 502
    assert response.status_code != 422
    assert response.status_code != 500
    assert "data: " not in response.text


@pytest.mark.asyncio
async def test_plain_prose_with_tools_remains_successful(mocker, install_gemini_client):
    client = _available_client(mocker, "I don't need a tool for this.")
    install_gemini_client(client)

    response = await _post({
        "model": "gemini-3-flash",
        "messages": [{"role": "user", "content": "Hello"}],
        "tools": [TOOLS[0]],
    })

    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "I don't need a tool for this."
    assert data["choices"][0]["finish_reason"] == "stop"
