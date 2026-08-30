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
    client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash",
        is_available=True,
    )
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
        SimpleNamespace(model_name="gemini/gemini-3-flash", is_available=True),
    ]
    client = _available_client(mocker)
    client.list_models.return_value = runtime_models
    install_gemini_client(client)

    response = await _get("/v1/stateless/models")

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    model_ids = [model["id"] for model in data["data"]]
    assert model_ids == ["gemini-available"]
    assert not any(model_id.startswith("atlas/") for model_id in model_ids)
    assert not any(model_id.startswith("playwright/") for model_id in model_ids)
    client.list_models.assert_called_once_with()


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
    assert 'Assistant called tool get_weather: {"location":"SF"}' in prompt
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
async def test_stateless_chat_rejects_gemini_provider_prefix_without_generation(
    mocker,
    install_gemini_client,
):
    client = _available_client(mocker)
    client.generate_content = mocker.AsyncMock()
    install_gemini_client(client)

    response = await _post(
        {
            "model": "gemini/gemini-3-flash",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )

    assert response.status_code == 400
    assert "not supported by the Gemini WebAPI endpoint" in response.json()["detail"]
    client.resolve_model.assert_not_called()
    client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_stateless_chat_rejects_unavailable_model_without_generation(
    mocker,
    install_gemini_client,
):
    client = mocker.Mock()
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
    assert "conversation_id" not in response.text
    client.generate_content_stream.assert_awaited_once_with(
        "User: Hello",
        "gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_stateless_chat_returns_openai_tool_calls_for_buffered_and_streaming_requests(
    mocker,
    install_gemini_client,
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
        }
    )

    assert response.status_code == 200
    if stream:
        assert "data: [DONE]\n\n" in response.text
    else:
        assert "data: [DONE]\n\n" not in response.text
    if stream:
        payload = next(
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: {")
        )
        message = payload["choices"][0]["message"]
    else:
        message = response.json()["choices"][0]["message"]

    tool_call = message["tool_calls"][0]
    assert tool_call["function"]["name"] == "get_weather"
    assert isinstance(tool_call["function"]["arguments"], str)
    assert json.loads(tool_call["function"]["arguments"]) == {"city": "SF"}
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
async def test_stateless_openapi_documents_both_routes(mocker, install_gemini_client):
    install_gemini_client(mocker.Mock())

    response = await _get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/stateless/models" in paths
    assert "/v1/stateless/chat/completions" in paths
    chat = paths["/v1/stateless/chat/completions"]["post"]
    assert "client-owned-history" in chat["description"]
    assert "temporary=True" in chat["description"]
    assert "Playwright" in chat["description"]
