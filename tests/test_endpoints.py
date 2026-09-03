import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import AsyncClient, ASGITransport
from gemini_webapi.exceptions import (
    APIError,
    AuthError,
    GeminiError,
    ModelInvalidError,
    TemporarilyBlockedError,
    TimeoutError as GeminiTimeoutError,
    UsageLimitExceededError,
)
from app.main import app
from app.services.factory import ProviderFactory
from app.services.providers.gemini.provider import GeminiProvider
import app.services.providers.gemini.client as gemini_client_module
import app.services.providers.gemini.temporary_chat as temporary_chat_module
import app.services.providers.gemini.stateless_chat as stateless_chat_module
from app.services.providers.atlas import AtlasProvider


def _ready_direct_client(mocker):
    client = mocker.Mock()
    client.client.account_status.name = "AVAILABLE"
    client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash",
        is_available=True,
    )
    return client


@pytest.mark.asyncio
async def test_temporary_stream_wrapper_failure_keeps_lease_with_caller(mocker, monkeypatch, install_registry_generation):
    client = mocker.Mock()
    client.close = mocker.AsyncMock()
    generation = install_registry_generation(client, generation=0)
    lease = gemini_client_module.acquire_gemini_lease(
        client=client,
        generation=generation,
    )
    record = gemini_client_module._gemini_generation_records[generation]
    gemini_client_module._retire_generation(record)
    cleanup = mocker.AsyncMock()
    monkeypatch.setattr(
        stateless_chat_module,
        "GeminiLeaseStreamingResponse",
        mocker.Mock(side_effect=RuntimeError("response construction failed")),
    )

    try:
        async with lease:
            with pytest.raises(RuntimeError, match="response construction failed"):
                await temporary_chat_module._build_incremental_streaming_response(
                    lease,
                    prompt="hello",
                    model="gemini-3-flash",
                    files=None,
                    gem=None,
                    cleanup_once=cleanup,
                )
    finally:
        if not lease.is_transferred:
            await cleanup()

    assert lease.is_transferred is False
    assert record.lease_count == 0
    cleanup.assert_awaited_once_with()
    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {
                "model": "playwright/gemini-3-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            id="legacy-provider-model-prefix",
        ),
        pytest.param(
            {
                "provider": "atlas",
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            id="unsupported-provider",
        ),
        pytest.param(
            {
                "model": "gemini-3-flash",
                "conversation_id": "existing-conversation",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            id="conversation-id",
        ),
    ],
)
async def test_stateless_validation_runs_before_lease(mocker, payload):
    acquire_lease = mocker.patch(
        "app.services.providers.gemini.stateless_chat.acquire_current_gemini_lease"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/stateless/chat/completions", json=payload)

    assert response.status_code == 400
    acquire_lease.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        pytest.param(
            "/v1/stateless/chat/completions",
            {
                "model": "gemini-3-flash",
                "provider_options": {"gemini": {"extended_thinking": True}},
                "messages": [{"role": "user", "content": "Hello"}],
            },
            id="provider-options",
        ),
        pytest.param(
            "/v1/temporary/chat/completions",
            {
                "model": "   ",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            id="whitespace-model",
        ),
        pytest.param(
            "/v1/temporary/chat/completions",
            {
                "model": "gemini/",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            id="empty-temporary-legacy-model",
        ),
    ],
)
async def test_temporary_validation_precedes_unavailable_lease(mocker, path, payload):
    acquire_lease = mocker.patch(
        "app.services.providers.gemini.stateless_chat.acquire_current_gemini_lease"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(path, json=payload)

    assert response.status_code == 400
    acquire_lease.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_name", ["UNAUTHENTICATED", "BLOCKED", "UNKNOWN"])
async def test_stateless_unready_client_maps_to_503(mocker, install_gemini_client, status_name):
    client = _ready_direct_client(mocker)
    client.client.account_status.name = status_name
    client.generate_content = mocker.AsyncMock()
    install_gemini_client(client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

    assert response.status_code == 503
    client.resolve_model.assert_not_called()
    client.generate_content.assert_not_awaited()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        pytest.param(AuthError("auth failed"), 503, id="auth"),
        pytest.param(asyncio.TimeoutError("request timed out"), 504, id="application-timeout"),
        pytest.param(GeminiTimeoutError("request timed out"), 504, id="gemini-timeout"),
        pytest.param(UsageLimitExceededError("quota exceeded"), 429, id="usage-limit"),
        pytest.param(TemporarilyBlockedError("temporarily blocked"), 429, id="temporarily-blocked"),
        pytest.param(ModelInvalidError("invalid model"), 502, id="invalid-model"),
        pytest.param(APIError("provider error"), 502, id="api-error"),
        pytest.param(GeminiError("server error"), 502, id="gemini-error"),
        pytest.param(RuntimeError("programming defect"), 500, id="unexpected-error"),
    ],
)
async def test_stateless_buffered_errors_map_to_http_status(
    mocker,
    install_gemini_client,
    error,
    expected_status,
):
    client = _ready_direct_client(mocker)
    client.generate_content = mocker.AsyncMock(side_effect=error)
    install_gemini_client(client)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

    assert response.status_code == expected_status
    cleanup.assert_awaited_once()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_stateless_buffered_timeout_releases_lease_and_cleans_up(mocker, install_gemini_client):
    async def never_returns(*args, **kwargs):
        await asyncio.sleep(60)

    client = _ready_direct_client(mocker)
    client.generate_content = mocker.AsyncMock(side_effect=never_returns)
    install_gemini_client(client)
    mocker.patch.object(stateless_chat_module, "DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS", 0.01)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

    assert response.status_code == 504
    cleanup.assert_awaited_once()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_stateless_progressive_timeout_terminates_without_done(mocker, install_gemini_client):
    async def response_stream():
        yield SimpleNamespace(text_delta="Partial response")
        await asyncio.sleep(60)

    client = _ready_direct_client(mocker)
    client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(client)
    mocker.patch.object(stateless_chat_module, "DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS", 0.01)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "stream": True,
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

    assert response.status_code == 200
    assert "Partial response" in response.text
    assert "data: [DONE]\n\n" not in response.text
    chunks = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["finish_reason"] is None
    assert chunks[0]["choices"][0]["delta"]["content"] == "Partial response"
    cleanup.assert_awaited_once()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_stateless_progressive_timeout_before_first_chunk_returns_clean_sse(
    mocker,
    install_gemini_client,
):
    async def response_stream():
        await asyncio.sleep(60)
        yield SimpleNamespace(text_delta="never sent")

    client = _ready_direct_client(mocker)
    client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(client)
    mocker.patch.object(stateless_chat_module, "DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS", 0.01)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "stream": True,
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

    assert response.status_code == 200
    assert response.text == ""
    assert "data: [DONE]\n\n" not in response.text
    cleanup.assert_awaited_once()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_progressive_timeout_does_not_cancel_blocked_downstream_send(
    mocker,
    install_gemini_client,
):
    async def response_stream():
        yield SimpleNamespace(text_delta="Partial response")
        await asyncio.sleep(60)

    client = _ready_direct_client(mocker)
    client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(client)
    mocker.patch.object(stateless_chat_module, "DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS", 0.01)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(),
    )
    lease = gemini_client_module.acquire_current_gemini_lease()
    normalized = stateless_chat_module.NormalizedOpenAIChatMessages(messages=[])
    cleanup_once = stateless_chat_module._build_cleanup_once(normalized)
    response = await stateless_chat_module._build_incremental_streaming_response(
        lease,
        prompt="User: Hello",
        model="gemini-3-flash",
        files=None,
        gem=None,
        cleanup_once=cleanup_once,
    )

    sent_messages = []
    first_chunk_send_started = asyncio.Event()
    resume_send = asyncio.Event()

    async def send(message):
        sent_messages.append(message)
        if message["type"] == "http.response.body" and message["body"]:
            first_chunk_send_started.set()
            await resume_send.wait()

    async def receive():
        await asyncio.sleep(3600)

    response_task = asyncio.create_task(
        response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )
    )
    await asyncio.wait_for(first_chunk_send_started.wait(), timeout=1)
    await asyncio.sleep(0.03)
    assert not response_task.done()

    resume_send.set()
    await asyncio.wait_for(response_task, timeout=1)

    assert any(
        message["type"] == "http.response.body" and b"Partial response" in message["body"]
        for message in sent_messages
    )
    assert not any(
        message["type"] == "http.response.body" and message["body"] == b"data: [DONE]\n\n"
        for message in sent_messages
    )
    cleanup.assert_awaited_once_with(normalized)
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_stateless_tool_stream_timeout_returns_504_without_fake_sse(
    mocker,
    install_gemini_client,
):
    async def never_returns(*args, **kwargs):
        await asyncio.sleep(60)

    client = _ready_direct_client(mocker)
    client.generate_content = mocker.AsyncMock(side_effect=never_returns)
    install_gemini_client(client)
    mocker.patch.object(stateless_chat_module, "DIRECT_WEBAPI_EXECUTION_TIMEOUT_SECONDS", 0.01)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "stream": True,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                "messages": [{"role": "user", "content": "Look this up."}],
            },
        )

    assert response.status_code == 504
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]\n\n" not in response.text
    cleanup.assert_awaited_once()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_stateless_cleanup_failure_does_not_mask_provider_error(
    mocker,
    install_gemini_client,
    caplog,
):
    client = _ready_direct_client(mocker)
    client.generate_content = mocker.AsyncMock(side_effect=APIError("provider error"))
    install_gemini_client(client)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(side_effect=RuntimeError("cleanup error")),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "Gemini WebAPI provider request failed."
    cleanup.assert_awaited_once()
    assert any("Error cleaning up Gemini temporary request" in record.message for record in caplog.records)
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_stateless_cleanup_owns_staged_files_after_tool_preparation_failure(
    mocker,
    install_gemini_client,
):
    from app.services.multimodal import cleanup_staged_files as real_cleanup_staged_files

    client = _ready_direct_client(mocker)
    client.generate_content = mocker.AsyncMock()
    install_gemini_client(client)
    mocker.patch.object(
        stateless_chat_module,
        "build_tools_prompt",
        side_effect=RuntimeError("tool preparation failed"),
    )
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(side_effect=real_cleanup_staged_files),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Summarize this file."},
                            {
                                "type": "file",
                                "file": {
                                    "filename": "invoice.pdf",
                                    "file_data": "data:application/pdf;base64,JVBERi0xLjQK",
                                },
                            },
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal error generating stateless content."
    cleanup.assert_awaited_once()
    normalized = cleanup.await_args.args[0]
    assert normalized.cleanup_dir is None
    assert not normalized.files[0].exists()
    client.generate_content.assert_not_awaited()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_stateless_staged_files_are_removed_after_provider_error(mocker, install_gemini_client):
    from app.services.multimodal import cleanup_staged_files as real_cleanup_staged_files

    client = _ready_direct_client(mocker)
    client.generate_content = mocker.AsyncMock(side_effect=APIError("provider error"))
    install_gemini_client(client)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(side_effect=real_cleanup_staged_files),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/stateless/chat/completions",
            json={
                "model": "gemini-3-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Summarize this file."},
                            {
                                "type": "file",
                                "file": {
                                    "filename": "invoice.pdf",
                                    "file_data": "data:application/pdf;base64,JVBERi0xLjQK",
                                },
                            },
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 502
    cleanup.assert_awaited_once()
    normalized = cleanup.await_args.args[0]
    assert normalized.cleanup_dir is None
    assert not normalized.files[0].exists()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_list_models_endpoint(mocker):
    """Verify /v1/models filters legacy Playwright Gemini aliases from discovery."""
    # Mock providers to return deterministic model lists
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.list_models = mocker.AsyncMock(return_value=[
        {"id": "gemini-model", "object": "model"},
        {"id": "playwright/gemini-3.5-flash", "object": "model"},
        {"id": "playwright/gemini-3.1-pro", "object": "model"},
        {"id": "playwright/gemini-3.1-flash-lite", "object": "model"},
        {"id": "playwright/gemini/gemini-3.5-flash", "object": "model"},
        {"id": "playwright/gemini/gemini-3.1-pro", "object": "model"},
        {"id": "playwright/gemini/gemini-3.1-flash-lite", "object": "model"},
    ])
    
    mock_atlas = mocker.Mock(spec=AtlasProvider)
    mock_atlas.list_models = mocker.AsyncMock(return_value=[{"id": "atlas-model", "object": "model"}])
    
    # Patch ProviderFactory._registry to use our mocks indirectly
    mocker.patch.dict(ProviderFactory._registry, {
        "gemini": lambda: mock_gemini,
        "atlas": lambda: mock_atlas
    })
    # Reset instances to force factory to use our patched registry
    ProviderFactory._instances = {}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/models")
    
    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    model_ids = [m["id"] for m in data["data"]]
    assert "gemini-model" in model_ids
    assert "atlas-model" in model_ids
    assert "playwright/gemini/gemini-3.5-flash" in model_ids
    assert "playwright/gemini/gemini-3.1-pro" in model_ids
    assert "playwright/gemini/gemini-3.1-flash-lite" in model_ids
    assert "playwright/gemini-3.5-flash" not in model_ids
    assert "playwright/gemini-3.1-pro" not in model_ids
    assert "playwright/gemini-3.1-flash-lite" not in model_ids

@pytest.mark.asyncio
async def test_chat_completions_endpoint_gemini(mocker):
    """Verify /v1/chat/completions delegates to the correct provider and returns OpenAI format."""
    mock_response = {"choices": [{"message": {"content": "Mocked Gemini response"}}]}
    
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.chat_completions = mocker.AsyncMock(return_value=mock_response)
    
    # Patch factory to return our mock provider
    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_gemini, "gemini-3-flash"))

    payload = {
        "model": "gemini-3-flash",
        "messages": [{"role": "user", "content": "Hello"}]
    }
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/chat/completions", json=payload)
    
    assert response.status_code == 200
    assert response.json() == mock_response
    mock_gemini.chat_completions.assert_called_once()

@pytest.mark.asyncio
async def test_chat_completions_endpoint_atlas(mocker):
    """Verify /v1/chat/completions delegates to Atlas when requested."""
    mock_response = {"choices": [{"message": {"content": "Mocked Atlas response"}}]}
    
    mock_atlas = mocker.Mock(spec=AtlasProvider)
    mock_atlas.chat_completions = mocker.AsyncMock(return_value=mock_response)
    
    # Patch factory to return our mock provider
    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_atlas, "MiniMax-M2"))

    payload = {
        "model": "atlas/MiniMax-M2",
        "messages": [{"role": "user", "content": "Hello"}]
    }
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/chat/completions", json=payload)
    
    assert response.status_code == 200
    assert response.json() == mock_response
    mock_atlas.chat_completions.assert_called_once()


@pytest.mark.asyncio
async def test_translate_endpoint_uses_temporary_gemini_requests(mocker, install_gemini_client):
    """Verify /translate forwards Gemini requests with temporary=True."""
    mock_response = SimpleNamespace(text="Translated response")

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_client.generate_content = mocker.AsyncMock(return_value=mock_response)

    generation = install_gemini_client(mock_client)
    assert gemini_client_module._gemini_client is mock_client
    assert gemini_client_module._current_gemini_generation == generation
    assert gemini_client_module._gemini_generation_records[generation].client is mock_client

    payload = {
        "model": "gemini-3-flash",
        "message": "Translate this text",
        "files": [],
        "gem": None,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/translate", json=payload)

    assert response.status_code == 200
    assert response.json() == {"response": "Translated response"}
    mock_client.generate_content.assert_awaited_once_with(
        "Translate this text",
        "gemini-3-flash",
        files=[],
        gem=None,
        temporary=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["unknown-model", "playwright/gemini-3.1-pro", "atlas/model"])
async def test_translate_rejects_invalid_direct_webapi_models_before_generation(mocker, install_gemini_client, model):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    if "/" not in model:
        mock_client.resolve_model.side_effect = ValueError(f"Unknown model name: {model}")
    mock_client.generate_content = mocker.AsyncMock()
    install_gemini_client(mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/translate", json={"model": model, "message": "Translate this text"})

    assert response.status_code == 400
    mock_client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gemini-3-flash", "flash"])
async def test_translate_rejects_known_unavailable_model_before_generation(mocker, install_gemini_client, model):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash", is_available=False
    )
    mock_client.generate_content = mocker.AsyncMock()
    install_gemini_client(mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/translate", json={"model": model, "message": "Translate this text"})

    assert response.status_code == 400
    assert response.json()["detail"] == f"Model '{model}' is not available for the current Gemini account or session."
    mock_client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "resolved_model"),
    [("flash", "gemini-3-flash"), ("pro", "gemini-3-pro"), ("thinking", "gemini-3-flash-thinking")],
)
async def test_translate_validates_and_preserves_aliases(mocker, install_gemini_client, model, resolved_model):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name=resolved_model, is_available=True)
    mock_client.generate_content = mocker.AsyncMock(return_value=SimpleNamespace(text="translated"))
    install_gemini_client(mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/translate", json={"model": model, "message": "Translate this text"})

    assert response.status_code == 200
    mock_client.resolve_model.assert_called_once_with(resolved_model)
    mock_client.generate_content.assert_awaited_once_with(
        "Translate this text",
        model,
        files=[],
        gem=None,
        temporary=True,
    )


@pytest.mark.asyncio
async def test_translate_client_unavailable_precedes_model_validation(mocker):
    gemini_client_module._initialization_error = "client unavailable"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/translate", json={"model": "unknown-model", "message": "Translate this text"})

    assert response.status_code == 503
    assert response.json()["detail"] == "client unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_name", "expected_status"),
    [("UNAUTHENTICATED", 401), ("BLOCKED", 503)],
)
async def test_translate_readiness_precedes_model_validation(mocker, install_gemini_client, status_name, expected_status):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = status_name
    mock_client.resolve_model = mocker.Mock()
    mock_client.generate_content = mocker.AsyncMock()
    install_gemini_client(mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/translate", json={"model": "unknown-model", "message": "Translate this text"})

    assert response.status_code == expected_status
    if status_name == "UNAUTHENTICATED":
        assert response.json()["detail"] == "Gemini authentication is required. Please sign in and try again."
        assert "conversation_id" not in response.json()["detail"]
        assert response.headers["WWW-Authenticate"] == "Bearer"
    mock_client.resolve_model.assert_not_called()
    mock_client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_translate_non_model_value_error_remains_500(mocker, install_gemini_client):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_client.generate_content = mocker.AsyncMock(side_effect=ValueError("generation failed"))
    install_gemini_client(mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/translate", json={"message": "Translate this text"})

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_translate_requests_execute_concurrently_and_independently(mocker, install_gemini_client):
    """Verify /translate uses independent direct Gemini requests."""
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def generate_content(message, model, *, files, gem, temporary):
        calls.append((message, model, files, gem, temporary))
        if len(calls) == 2:
            entered.set()
        await release.wait()
        return SimpleNamespace(text=f"translated:{message}")

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_client.generate_content = generate_content
    install_gemini_client(mock_client)
    mocker.patch(
        "app.services.providers.gemini.session_manager.SessionManager.get_response",
        side_effect=AssertionError("/translate must not use SessionManager"),
    )

    payloads = [
        {"model": "gemini-3-flash", "message": "alpha", "files": ["a.txt"], "gem": "gem-a"},
        {"model": "gemini-3-pro", "message": "beta", "files": ["b.txt"], "gem": "gem-b"},
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        tasks = [asyncio.create_task(ac.post("/translate", json=payload)) for payload in payloads]
        await asyncio.wait_for(entered.wait(), timeout=1)
        release.set()
        responses = await asyncio.gather(*tasks)

    assert [response.status_code for response in responses] == [200, 200]
    assert [response.json() for response in responses] == [
        {"response": "translated:alpha"},
        {"response": "translated:beta"},
    ]
    assert sorted(calls) == sorted([
        ("alpha", "gemini-3-flash", ["a.txt"], "gem-a", True),
        ("beta", "gemini-3-pro", ["b.txt"], "gem-b", True),
    ])


@pytest.mark.asyncio
async def test_translate_failure_does_not_block_other_request(mocker, install_gemini_client):
    """Verify one direct translation failure does not affect another request."""
    async def generate_content(message, model, *, files, gem, temporary):
        if message == "bad":
            raise RuntimeError("translation failed")
        return SimpleNamespace(text="good result")

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_client.generate_content = generate_content
    install_gemini_client(mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        bad, good = await asyncio.gather(
            ac.post("/translate", json={"message": "bad"}),
            ac.post("/translate", json={"message": "good"}),
        )

    assert bad.status_code == 500
    assert good.status_code == 200
    assert good.json() == {"response": "good result"}


@pytest.mark.asyncio
async def test_temporary_chat_completions_endpoint_non_streaming(mocker, install_gemini_client):
    """Verify /v1/temporary/chat/completions uses temporary Gemini WebAPI requests and preserves artifacts."""
    mock_response = SimpleNamespace(
        text="Temporary Gemini response",
        images=[
            SimpleNamespace(
                url="https://example.invalid/temp-image.png",
                title="Temporary image",
                alt="Temporary preview",
            )
        ],
        videos=[],
        media=[],
    )

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_client.generate_content = mocker.AsyncMock(return_value=mock_response)
    mock_client.generate_content_stream = mocker.AsyncMock()

    install_gemini_client(mock_client)
    mocker.patch(
        "app.endpoints.chat.ProviderFactory.get_provider",
        side_effect=AssertionError("ProviderFactory must not be used by /v1/temporary/chat/completions"),
    )
    mocker.patch(
        "app.services.providers.gemini.provider.GeminiProvider.chat_completions",
        side_effect=AssertionError("GeminiProvider must not be used by /v1/temporary/chat/completions"),
    )
    mocker.patch(
        "app.services.providers.gemini.webapi_adapter.GeminiWebAPIAdapter.chat_completions",
        side_effect=AssertionError("GeminiWebAPIAdapter must not be used by /v1/temporary/chat/completions"),
    )
    mocker.patch(
        "app.services.providers.gemini.session_manager.SessionRegistry.save_session_snapshot",
        side_effect=AssertionError("SessionRegistry snapshot persistence must not be used"),
    )

    payload = {
        "model": "gemini-3-flash",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/temporary/chat/completions", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Temporary Gemini response"
    assert data["choices"][0]["artifacts"] == [
        {
            "type": "image",
            "provider": "gemini_webapi",
            "title": "Temporary image",
            "url": "https://example.invalid/temp-image.png",
            "alt": "Temporary preview",
        }
    ]
    mock_client.generate_content.assert_awaited_once_with(
        "User: Hello",
        "gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )
    mock_client.generate_content_stream.assert_not_called()


@pytest.mark.asyncio
async def test_temporary_chat_completions_endpoint_rejects_unknown_model_before_generation(mocker, install_gemini_client):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.side_effect = ValueError("Unknown model name: gemini-3")
    mock_client.generate_content = mocker.AsyncMock()
    install_gemini_client(mock_client)

    payload = {
        "model": "gemini-3",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/temporary/chat/completions", json=payload)

    assert response.status_code == 400
    assert "Unknown model name: gemini-3" in response.json()["detail"]
    mock_client.generate_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_temporary_chat_completions_rejects_known_unavailable_model_before_generation(mocker, install_gemini_client):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(
        model_name="gemini-3-flash", is_available=False
    )
    mock_client.generate_content = mocker.AsyncMock()
    install_gemini_client(mock_client)

    payload = {
        "model": "gemini-3-flash",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/temporary/chat/completions", json=payload)

    assert response.status_code == 400
    mock_client.generate_content.assert_not_awaited()
@pytest.mark.asyncio
async def test_temporary_chat_completions_endpoint_streaming(mocker, install_gemini_client):
    """Verify /v1/temporary/chat/completions streams SSE chunks and forwards temporary=True."""
    async def mock_stream():
        yield SimpleNamespace(
            text_delta="Temporary delta",
            images=[],
            videos=[],
            media=[],
        )
        yield SimpleNamespace(
            text="Temporary streamed response",
            images=[
                SimpleNamespace(
                    url="https://example.invalid/stream-image.png",
                    title="Stream image",
                    alt="Stream preview",
                )
            ],
            videos=[],
            media=[],
        )

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_client.generate_content = mocker.AsyncMock()
    mock_client.generate_content_stream = mocker.AsyncMock(return_value=mock_stream())

    install_gemini_client(mock_client)
    mocker.patch(
        "app.endpoints.chat.ProviderFactory.get_provider",
        side_effect=AssertionError("ProviderFactory must not be used by /v1/temporary/chat/completions"),
    )
    mocker.patch(
        "app.services.providers.gemini.provider.GeminiProvider.chat_completions",
        side_effect=AssertionError("GeminiProvider must not be used by /v1/temporary/chat/completions"),
    )
    mocker.patch(
        "app.services.providers.gemini.webapi_adapter.GeminiWebAPIAdapter.chat_completions",
        side_effect=AssertionError("GeminiWebAPIAdapter must not be used by /v1/temporary/chat/completions"),
    )
    mocker.patch(
        "app.services.providers.gemini.session_manager.SessionRegistry.save_session_snapshot",
        side_effect=AssertionError("SessionRegistry snapshot persistence must not be used"),
    )

    payload = {
        "model": "gemini-3-flash",
        "stream": True,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream("POST", "/v1/temporary/chat/completions", json=payload) as response:
            assert response.status_code == 200
            body = await response.aread()

    body_text = body.decode()
    assert "data: " in body_text
    assert "Temporary delta" in body_text
    assert "artifacts" in body_text
    assert body_text.count("data: [DONE]\n\n") == 1
    mock_client.generate_content_stream.assert_awaited_once_with(
        "User: Hello",
        "gemini-3-flash",
        files=None,
        gem=None,
        temporary=True,
    )
    mock_client.generate_content.assert_not_called()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_text", [None, "Partial delta"], ids=["before-first-chunk", "after-chunk"])
async def test_temporary_stream_gemini_failure_terminates_without_done(
    mocker,
    install_gemini_client,
    caplog,
    partial_text,
):
    async def response_stream():
        if partial_text is not None:
            yield SimpleNamespace(text_delta=partial_text)
        raise APIError("Gemini stream failed")

    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(mock_client)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(),
    )

    payload = {
        "model": "gemini-3-flash",
        "stream": True,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream("POST", "/v1/temporary/chat/completions", json=payload) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = await response.aread()

    body_text = body.decode()
    assert "data: [DONE]\n\n" not in body_text
    if partial_text is not None:
        assert partial_text in body_text
    assert any(
        "progressive streaming terminal failure" in record.message
        for record in caplog.records
    )
    cleanup.assert_awaited_once()
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_temporary_stream_cancellation_preserves_cancel_and_releases_lease(
    mocker,
    install_gemini_client,
):
    chunk_sent = asyncio.Event()
    blocked = asyncio.Event()

    async def response_stream():
        yield SimpleNamespace(text_delta="Partial delta")
        await blocked.wait()

    mock_client = mocker.Mock()
    mock_client.generate_content_stream = mocker.AsyncMock(return_value=response_stream())
    install_gemini_client(mock_client)
    cleanup = mocker.patch(
        "app.services.providers.gemini.stateless_chat.cleanup_staged_files",
        mocker.AsyncMock(),
    )
    lease = gemini_client_module.acquire_current_gemini_lease()
    normalized = stateless_chat_module.NormalizedOpenAIChatMessages(messages=[])
    cleanup_once = stateless_chat_module._build_cleanup_once(normalized)
    response = await stateless_chat_module._build_incremental_streaming_response(
        lease,
        prompt="User: Hello",
        model="gemini-3-flash",
        files=None,
        gem=None,
        cleanup_once=cleanup_once,
    )

    messages = []

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and message["body"]:
            chunk_sent.set()

    async def receive():
        await asyncio.sleep(3600)

    response_task = asyncio.create_task(
        response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )
    )
    await chunk_sent.wait()
    response_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await response_task

    assert any(message["type"] == "http.response.start" for message in messages)
    assert not any(
        message.get("body") == b"data: [DONE]\n\n"
        for message in messages
        if message["type"] == "http.response.body"
    )
    cleanup.assert_awaited_once_with(normalized)
    assert gemini_client_module._gemini_generation_records[0].lease_count == 0


@pytest.mark.asyncio
async def test_temporary_chat_completions_endpoint_rejects_conversation_id(mocker, install_gemini_client):
    install_gemini_client(mocker.Mock())
    payload = {
        "model": "gemini-3-flash",
        "conversation_id": "existing-conversation",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/temporary/chat/completions", json=payload)

    assert response.status_code == 400
    assert "conversation_id" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, detail_fragment",
    [
        (
            {
                "model": "playwright/gemini-3-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            "Playwright models",
        ),
        (
            {
                "model": "atlas/MiniMax-M2",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            "Atlas models",
        ),
        (
            {
                "provider": "atlas",
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            "Only the Gemini provider",
        ),
    ],
)
async def test_temporary_chat_completions_endpoint_rejects_unsupported_providers(
    mocker,
    install_gemini_client,
    payload,
    detail_fragment,
):
    install_gemini_client(mocker.Mock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/temporary/chat/completions", json=payload)

    assert response.status_code == 400
    assert detail_fragment in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_conversation_endpoint_gemini(mocker):
    """Verify /v1/conversations/{conversation_id} delegates to Gemini deletion."""
    mock_response = {
        "id": "conv-delete",
        "object": "conversation.deleted",
        "deleted": True,
        "provider": "gemini",
        "backend": "webapi",
    }
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.delete_conversation = mocker.AsyncMock(return_value=mock_response)

    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_gemini, ""))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.delete("/v1/conversations/conv-delete")

    assert response.status_code == 200
    assert response.json() == mock_response
    mock_gemini.delete_conversation.assert_called_once_with("conv-delete")


@pytest.mark.asyncio
async def test_list_conversations_endpoint_gemini_empty(mocker):
    """Verify /v1/conversations delegates to Gemini conversation listing."""
    mock_response = {
        "object": "list",
        "provider": "gemini",
        "backend": "webapi",
        "count": 0,
        "data": [],
    }
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.list_conversations = mocker.AsyncMock(return_value=mock_response)

    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_gemini, ""))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/conversations")

    assert response.status_code == 200
    assert response.json() == mock_response
    mock_gemini.list_conversations.assert_called_once_with()


@pytest.mark.asyncio
async def test_delete_conversations_endpoint_gemini(mocker):
    """Verify DELETE /v1/conversations delegates to Gemini bulk deletion."""
    mock_response = {
        "object": "conversation.bulk_delete",
        "provider": "gemini",
        "backend": "webapi",
        "total": 0,
        "deleted_count": 0,
        "failed_count": 0,
        "skipped_active_count": 0,
        "results": [],
    }
    mock_gemini = mocker.Mock(spec=GeminiProvider)
    mock_gemini.delete_conversations = mocker.AsyncMock(return_value=mock_response)

    mocker.patch("app.services.factory.ProviderFactory.get_provider", return_value=(mock_gemini, ""))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.delete("/v1/conversations")

    assert response.status_code == 200
    assert response.json() == mock_response
    mock_gemini.delete_conversations.assert_called_once_with()
