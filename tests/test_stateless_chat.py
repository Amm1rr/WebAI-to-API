import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
import app.services.gemini_client as gemini_client_module


async def _get(path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


async def _post(payload: dict, path: str = "/v1/stateless/chat/completions"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=payload)


def _available_client(mocker):
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"
    client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash",
        is_available=True,
    )
    return client


def _client_with_resolver(mocker, available_models: set[str]):
    """Create client where only models in available_models are reported available."""
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"

    def _resolve(name: str):
        if name in available_models:
            return SimpleNamespace(model_name=name, is_available=True)
        # Simulate unknown model – raise ValueError as real client does
        raise ValueError(f"Unknown model name: {name}")

    client.resolve_model.side_effect = _resolve
    return client


@pytest.mark.asyncio
async def test_stateless_models_return_only_available_direct_gemini_models(
    mocker,
    install_gemini_client,
):
    runtime_models = [
        SimpleNamespace(model_name="gemini-available", is_available=True),
        SimpleNamespace(model_name="gemini-unavailable", is_available=False),
        SimpleNamespace(model_name="playwright/gemini-3.1-pro", is_available=True),
        SimpleNamespace(model_name="playwright/gemini/gemini-3.1-pro", is_available=True),
        SimpleNamespace(model_name="atlas/MiniMax-M2", is_available=True),
        SimpleNamespace(model_name="my-custom/slash-model", is_available=True),
        SimpleNamespace(model_name="unknown/slash-model", is_available=True),
    ]
    client = _available_client(mocker)
    client.list_models.return_value = runtime_models
    install_gemini_client(client)

    response = await _get("/v1/stateless/models")

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    model_ids = [model["id"] for model in data["data"]]
    # Slash-containing direct WebAPI models are now included; only playwright/atlas excluded
    assert model_ids == ["gemini-available", "my-custom/slash-model", "unknown/slash-model"]
    assert not any(model_id.startswith("atlas/") for model_id in model_ids)
    assert not any(model_id.startswith("playwright/") for model_id in model_ids)
    client.list_models.assert_called_once_with()


@pytest.mark.asyncio
async def test_stateless_models_include_valid_slash_models_and_exclude_playwright_atlas(
    mocker,
    install_gemini_client,
):
    # Explicit regression for slash handling: valid slash IDs advertised, routing IDs excluded
    runtime_models = [
        SimpleNamespace(model_name="gemini-3-flash", is_available=True),
        SimpleNamespace(model_name="exp/slash-variant", is_available=True),
        SimpleNamespace(model_name="team/custom-model", is_available=True),
        SimpleNamespace(model_name="playwright/gemini-3.1-pro", is_available=True),
        SimpleNamespace(model_name="atlas/MiniMax-M2", is_available=True),
    ]
    client = _available_client(mocker)
    client.list_models.return_value = runtime_models
    install_gemini_client(client)

    response = await _get("/v1/stateless/models")
    model_ids = [m["id"] for m in response.json()["data"]]
    assert "exp/slash-variant" in model_ids
    assert "team/custom-model" in model_ids
    assert "gemini-3-flash" in model_ids
    assert not any(mid.startswith("playwright/") for mid in model_ids)
    assert not any(mid.startswith("atlas/") for mid in model_ids)


@pytest.mark.asyncio
async def test_stateless_models_are_accepted_by_stateless_chat_validation(
    mocker,
    install_gemini_client,
):
    client = _available_client(mocker)
    client.list_models.return_value = [
        SimpleNamespace(model_name="gemini-available", is_available=True),
    ]
    client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text="ok"))
    install_gemini_client(client)

    catalog_response = await _get("/v1/stateless/models")
    model_id = catalog_response.json()["data"][0]["id"]
    response = await _post(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert response.status_code == 200
    client.resolve_model.assert_called_once_with(model_id)
    client.generate_content.assert_awaited_once_with(
        "User: Hello",
        model_id,
        files=None,
        gem=None,
        temporary=True,
    )


@pytest.mark.asyncio
async def test_stateless_models_slash_advertised_are_valid_for_chat(
    mocker,
    install_gemini_client,
):
    # Every model advertised must be accepted under same runtime state (slash variant)
    slash_model = "my-team/slash-model"
    runtime_models = [SimpleNamespace(model_name=slash_model, is_available=True)]
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"
    client.list_models.return_value = runtime_models
    client.resolve_model.return_value = SimpleNamespace(model_name=slash_model, is_available=True)
    client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text="ok"))
    install_gemini_client(client)

    catalog_response = await _get("/v1/stateless/models")
    assert catalog_response.json()["data"][0]["id"] == slash_model

    response = await _post(
        {"model": slash_model, "messages": [{"role": "user", "content": "Hello"}]}
    )
    assert response.status_code == 200
    client.resolve_model.assert_called_with(slash_model)
    client.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_stateless_chat_uses_shared_temporary_execution_without_persistence(
    mocker,
    install_gemini_client,
):
    client = _available_client(mocker)
    client.generate_content = mocker.AsyncMock(
        return_value=SimpleNamespace(text="Stateless response")
    )
    install_gemini_client(client)
    mocker.patch(
        "app.services.providers.gemini.session_manager.SessionRegistry.get_session",
        side_effect=AssertionError("stateless requests must not restore sessions"),
    )
    mocker.patch(
        "app.services.providers.gemini.session_manager.SessionRegistry.save_session_snapshot",
        side_effect=AssertionError("stateless requests must not save snapshots"),
    )

    response = await _post(
        {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "Stateless response"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "usage" not in data
    assert "conversation_id" not in data
    assert "reused_conversation" not in data
    client.generate_content.assert_awaited_once_with(
        "User: Hello",
        "gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_stateless_chat_transforms_full_history_and_tool_loop(mocker, install_gemini_client):
    client = _available_client(mocker)
    client.generate_content = mocker.AsyncMock(
        return_value=SimpleNamespace(text="Final answer")
    )
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "What is the weather?"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"location":"SF"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "Sunny, 18C",
                },
                {"role": "user", "content": "Summarize that."},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get current weather.",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        }
    )

    assert response.status_code == 200
    prompt = client.generate_content.await_args.args[0]
    assert "System: Be concise." in prompt
    assert "You have access to the following tools." in prompt
    assert "User: What is the weather?" in prompt
    assert 'Assistant tool call [call-1] get_weather: {"location":"SF"}' in prompt
    assert "Tool result [call-1]: Sunny, 18C" in prompt
    assert "User: Summarize that." in prompt
    assert client.generate_content.await_args.kwargs["temporary"] is True


@pytest.mark.asyncio
async def test_stateless_chat_rejects_conversation_id_without_generation(
    mocker,
    install_gemini_client,
):
    client = _available_client(mocker)
    client.generate_content = mocker.AsyncMock()
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "conversation_id": "previous-conversation",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert response.status_code == 400
    assert "conversation_id" in response.json()["detail"]
    assert "stateless chat endpoint" in response.json()["detail"]
    client.resolve_model.assert_not_called()
    client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_stateless_chat_accepts_available_slash_model(
    mocker,
    install_gemini_client,
):
    slash_model = "my-team/slash-model"
    client = _client_with_resolver(mocker, {slash_model, "gemini-3-flash"})
    client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text="ok"))
    install_gemini_client(client)

    response = await _post(
        {
            "model": slash_model,
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert response.status_code == 200
    client.resolve_model.assert_called_once_with(slash_model)
    client.generate_content.assert_awaited_once_with(
        "User: Hello",
        slash_model,
        files=None,
        gem=None,
        temporary=True,
    )


@pytest.mark.asyncio
async def test_stateless_chat_rejects_unknown_slash_model(
    mocker,
    install_gemini_client,
):
    client = _client_with_resolver(mocker, {"gemini-3-flash"})
    client.generate_content = mocker.AsyncMock()
    install_gemini_client(client)

    response = await _post(
        {
            "model": "unknown/slash-model",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert response.status_code == 400
    assert "Unknown model" in response.json()["detail"] or "not available" in response.json()["detail"].lower()
    client.resolve_model.assert_called_once_with("unknown/slash-model")
    client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_stateless_chat_rejects_unavailable_model_without_generation(
    mocker,
    install_gemini_client,
):
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"
    client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-unavailable",
        is_available=False,
    )
    client.generate_content = mocker.AsyncMock()
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-unavailable",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert response.status_code == 400
    assert "not available" in response.json()["detail"]
    client.resolve_model.assert_called_once_with("gemini-unavailable")
    client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "model": "playwright/gemini-3.1-pro",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        {
            "model": "playwright/gemini/gemini-3.1-pro",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        {
            "model": "atlas/MiniMax-M2",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        {
            "provider": "atlas",
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    ],
)
async def test_stateless_chat_rejects_non_gemini_execution_targets(
    mocker,
    install_gemini_client,
    payload,
):
    client = _available_client(mocker)
    client.generate_content = mocker.AsyncMock()
    install_gemini_client(client)

    response = await _post(payload)

    assert response.status_code == 400
    client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_stateless_chat_streams_sse_and_terminates_with_done(mocker, install_gemini_client):
    async def response_stream():
        yield SimpleNamespace(text_delta="Hello")
        yield SimpleNamespace(text_delta=" world", text="Hello world")

    client = _available_client(mocker)
    client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: " in response.text
    assert "Hello" in response.text
    assert "data: [DONE]\n\n" in response.text
    assert response.text.count("data: [DONE]\n\n") == 1
    assert "conversation_id" not in response.text
    chunks = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert len(chunks) == 3
    assert [chunk["choices"][0]["delta"]["content"] for chunk in chunks[:-1]] == [
        "Hello",
        " world",
    ]
    assert all(chunk["choices"][0]["finish_reason"] is None for chunk in chunks[:-1])
    terminal = chunks[-1]
    assert terminal["choices"][0]["delta"] == {}
    assert terminal["choices"][0]["finish_reason"] == "stop"
    assert len({chunk["id"] for chunk in chunks}) == 1
    assert chunks[0]["id"].startswith("chatcmpl-")
    assert len({chunk["created"] for chunk in chunks}) == 1
    assert {chunk["model"] for chunk in chunks} == {"gemini-3-flash"}
    assert all("message" not in chunk["choices"][0] for chunk in chunks)
    client.generate_content_stream.assert_awaited_once_with(
        "User: Hello",
        "gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )


@pytest.mark.asyncio
async def test_stateless_empty_progressive_stream_emits_terminal_stop(mocker, install_gemini_client):
    async def response_stream():
        if False:
            yield SimpleNamespace(text_delta="")

    client = _available_client(mocker)
    client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    chunks = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert response.status_code == 200
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"] == {}
    assert chunks[0]["choices"][0]["finish_reason"] == "stop"
    assert response.text.count("data: [DONE]\n\n") == 1


@pytest.mark.asyncio
async def test_stateless_artifact_only_progressive_stream_uses_one_terminal_chunk(
    mocker,
    install_gemini_client,
):
    async def response_stream():
        yield SimpleNamespace(
            text_delta="",
            images=[SimpleNamespace(url="https://example.com/image.png", title="Generated")],
            videos=[],
            media=[],
        )

    client = _available_client(mocker)
    client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "stream": True,
            "messages": [{"role": "user", "content": "Draw this"}],
        }
    )

    chunks = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert response.status_code == 200
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"] == {}
    assert chunks[0]["choices"][0]["finish_reason"] == "stop"
    assert chunks[0]["choices"][0]["artifacts"][0]["url"] == "https://example.com/image.png"
    assert response.text.count("data: [DONE]\n\n") == 1


@pytest.mark.asyncio
async def test_stateless_text_and_artifact_progressive_stream_has_one_terminal_stop(
    mocker,
    install_gemini_client,
):
    async def response_stream():
        yield SimpleNamespace(text_delta="Hello", images=[], videos=[], media=[])
        yield SimpleNamespace(
            text_delta="",
            images=[SimpleNamespace(url="https://example.com/image.png", title="Generated")],
            videos=[],
            media=[],
        )

    client = _available_client(mocker)
    client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "stream": True,
            "messages": [{"role": "user", "content": "Describe this"}],
        }
    )

    chunks = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert response.status_code == 200
    assert len(chunks) == 2
    assert chunks[0]["choices"][0]["delta"]["content"] == "Hello"
    assert chunks[0]["choices"][0]["finish_reason"] is None
    assert chunks[1]["choices"][0]["delta"] == {}
    assert chunks[1]["choices"][0]["finish_reason"] == "stop"
    assert "artifacts" in chunks[1]["choices"][0]
    assert sum(chunk["choices"][0]["finish_reason"] == "stop" for chunk in chunks) == 1
    assert len({chunk["id"] for chunk in chunks}) == 1
    assert len({chunk["created"] for chunk in chunks}) == 1
    assert response.text.count("data: [DONE]\n\n") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/stateless/chat/completions",
        "/v1/temporary/chat/completions",
    ],
)
@pytest.mark.parametrize("stream", [False, True])
async def test_temporary_and_stateless_chat_return_openai_tool_calls(
    mocker,
    install_gemini_client,
    path,
    stream,
):
    tool_response = '{"tool_call": {"name": "get_weather", "arguments": {"city": "SF"}}}'
    client = _available_client(mocker)
    client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text=tool_response))
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini-3-flash",
            "stream": stream,
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        },
        path=path,
    )

    assert response.status_code == 200
    if stream:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "data: [DONE]\n\n" in response.text
        payload = next(
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: {")
        )
        assert payload["object"] == "chat.completion.chunk"
        choice = payload["choices"][0]
        assert "message" not in choice
        assert choice["finish_reason"] == "tool_calls"
        message = choice["delta"]
        assert "usage" not in payload
    else:
        assert "data: [DONE]\n\n" not in response.text
        data = response.json()
        assert data["object"] == "chat.completion"
        choice = data["choices"][0]
        assert "delta" not in choice
        assert choice["finish_reason"] == "tool_calls"
        message = choice["message"]
        assert "usage" not in data

    assert message["role"] == "assistant"
    assert message["content"] is None
    tool_call = message["tool_calls"][0]
    assert tool_call["id"].startswith("call_")
    assert tool_call["function"]["name"] == "get_weather"
    assert isinstance(tool_call["function"]["arguments"], str)
    assert json.loads(tool_call["function"]["arguments"]) == {"city": "SF"}
    if stream:
        assert tool_call["index"] == 0
    else:
        assert "index" not in tool_call
    prompt = client.generate_content.await_args.args[0]
    assert "You have access to the following tools." in prompt
    assert prompt.endswith("User: Weather?")
    assert client.generate_content.await_args.args[1] == "gemini-3-flash"
    assert client.generate_content.await_args.kwargs == {
        "files": None,
        "gem": None,
        "temporary": True,
    }


@pytest.mark.asyncio
async def test_temporary_chat_preserves_gemini_model_prefix_compatibility(
    mocker,
    install_gemini_client,
):
    client = _available_client(mocker)
    client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text="ok"))
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini/gemini-3-flash",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        path="/v1/temporary/chat/completions",
    )

    assert response.status_code == 200
    client.resolve_model.assert_called_once_with("gemini-3-flash")
    client.generate_content.assert_awaited_once_with(
        "User: Hello",
        "gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )


@pytest.mark.asyncio
async def test_temporary_endpoint_still_works_and_delegates_to_stateless(
    mocker,
    install_gemini_client,
):
    client = _available_client(mocker)
    client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text="ok"))
    install_gemini_client(client)

    response = await _post(
        {"model": "gemini-3-flash", "messages": [{"role": "user", "content": "hi"}]},
        path="/v1/temporary/chat/completions",
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"
    client.generate_content.assert_awaited_once()
    assert client.generate_content.await_args.kwargs["temporary"] is True


@pytest.mark.asyncio
async def test_temporary_openapi_is_deprecated(mocker, install_gemini_client):
    install_gemini_client(mocker.Mock())
    response = await _get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert paths["/v1/temporary/chat/completions"]["post"].get("deprecated") is True
    assert "deprecated" in paths["/v1/temporary/chat/completions"]["post"]["description"].lower()
    assert "/v1/stateless/chat/completions" in paths["/v1/temporary/chat/completions"]["post"]["description"]


@pytest.mark.asyncio
async def test_stateless_openapi_documents_both_routes(mocker, install_gemini_client):
    install_gemini_client(mocker.Mock())

    response = await _get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/stateless/models" in paths
    assert "/v1/stateless/chat/completions" in paths
    chat = paths["/v1/stateless/chat/completions"]["post"]
    assert "client-owned-history" in chat["description"].lower()
    assert "temporary=True" in chat["description"]
    assert "Playwright" in chat["description"]
    assert "slash" in chat["description"].lower()
