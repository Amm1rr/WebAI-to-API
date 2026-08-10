import json

import pytest
from types import SimpleNamespace
from fastapi import HTTPException

from app.schemas.request import OpenAIChatRequest
from app.services.providers.exceptions import (
    ConversationInUseError,
    SnapshotNotFoundError,
    StateIntegrityError,
)
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.sqlite_repository import SQLiteConversationRepository
from app.services.providers.gemini.session_manager import SessionManager, SessionRegistry


class MockResponse:
    def __init__(self, text):
        self.text = text


class MockChatSession:
    def __init__(self, metadata, model, gem=None):
        self._ChatSession__metadata = metadata
        self.model = model
        self.gem = gem
        self.prompts = []
        self.files_received = []

    @property
    def metadata(self):
        return self._ChatSession__metadata

    async def send_message(self, prompt, files=None, temporary=False, deep_research=False, **kwargs):
        self.prompts.append(prompt)
        self.files_received.append(files)
        self.metadata[0] = "cid-restored"
        self.metadata[1] = f"rid-{len(self.prompts)}"
        self.metadata[2] = f"rcid-{len(self.prompts)}"
        self.metadata[9] = f"context-{len(self.prompts)}"
        return MockResponse(f"response: {prompt}")


class MockGeminiClient:
    def __init__(self, initial_metadata_factory=None):
        self.sessions = []
        self.client = SimpleNamespace(
            account_status=SimpleNamespace(name="AVAILABLE")
        )
        self.initial_metadata_factory = initial_metadata_factory or (
            lambda: ["", "", "", None, None, None, None, None, None, ""]
        )

    def start_chat(self, model, gem=None):
        session = MockChatSession(self.initial_metadata_factory(), model, gem)
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_session_manager_get_response_passes_temporary_flag(mocker):
    mock_session = mocker.Mock()
    mock_session.send_message = mocker.AsyncMock(return_value=MockResponse("ok"))

    mock_client = mocker.Mock()
    mock_client.start_chat = mocker.Mock(return_value=mock_session)

    manager = SessionManager(mock_client)

    response = await manager.get_response(
        "gemini-3-flash",
        "hello",
        None,
        temporary=True,
    )

    assert response.text == "ok"
    mock_session.send_message.assert_awaited_once_with(
        prompt="hello",
        files=None,
        temporary=True,
    )


@pytest.mark.asyncio
async def test_restart_recovery_reuses_snapshot_and_sends_only_final_message(mocker):
    provider = GeminiProvider()
    client = MockGeminiClient()
    saved_snapshots = []
    first_repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    first_registry = SessionRegistry(client, repository=first_repo)

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=first_registry)
    mocker.patch("app.services.providers.gemini.provider.generate_opaque_token", return_value="conv-restart")

    first_response = await provider.chat_completions(
        OpenAIChatRequest(
            messages=[{"role": "user", "content": "Remember alpha"}],
            model="gemini-3-flash",
        )
    )

    assert first_response["conversation_id"] == "conv-restart"
    assert first_response["reused_conversation"] is False

    saved_snapshot = saved_snapshots[0]
    second_repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=saved_snapshot),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    second_registry = SessionRegistry(client, repository=second_repo)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=second_registry)

    second_response = await provider.chat_completions(
        OpenAIChatRequest(
            messages=[
                {"role": "user", "content": "Remember alpha"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "What did I ask you to remember?"},
            ],
            model="gemini-3-flash",
            conversation_id="conv-restart",
        )
    )

    assert second_response["reused_conversation"] is True
    assert client.sessions[-1].prompts == ["What did I ask you to remember?"]


@pytest.mark.asyncio
async def test_restart_recovery_reuses_snapshot_and_passes_file_on_current_turn(mocker):
    provider = GeminiProvider()
    client = MockGeminiClient()
    saved_snapshots = []
    first_repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    first_registry = SessionRegistry(client, repository=first_repo)

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=first_registry)
    mocker.patch("app.services.providers.gemini.provider.generate_opaque_token", return_value="conv-restart-file")

    first_response = await provider.chat_completions(
        OpenAIChatRequest(
            messages=[{"role": "user", "content": "Remember alpha"}],
            model="gemini-3-flash",
        )
    )

    assert first_response["conversation_id"] == "conv-restart-file"
    assert first_response["reused_conversation"] is False

    saved_snapshot = saved_snapshots[0]
    second_repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=saved_snapshot),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    second_registry = SessionRegistry(client, repository=second_repo)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=second_registry)

    second_response = await provider.chat_completions(
        OpenAIChatRequest(
            messages=[
                {"role": "user", "content": "Remember alpha"},
                {"role": "assistant", "content": "ok"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What does this file say?"},
                        {"type": "file", "file": {"filename": "invoice.pdf", "file_data": "data:application/pdf;base64,JVBERi0xLjQK"}},
                    ],
                },
            ],
            model="gemini-3-flash",
            conversation_id="conv-restart-file",
        )
    )

    assert second_response["reused_conversation"] is True
    assert client.sessions[-1].prompts == ["What does this file say?"]
    assert len(client.sessions[-1].files_received[-1]) == 1


@pytest.mark.asyncio
async def test_registry_fails_closed_when_requested_snapshot_is_missing(mocker):
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    registry = SessionRegistry(MockGeminiClient(), repository=repo)

    with pytest.raises(SnapshotNotFoundError):
        await registry.get_session(
            "missing-conversation",
            GeminiProvider(),
            allow_create=False,
            model="gemini-3-flash",
        )


@pytest.mark.asyncio
async def test_provider_returns_recovery_error_for_missing_snapshot(mocker):
    provider = GeminiProvider()
    client = MockGeminiClient()
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    registry = SessionRegistry(client, repository=repo)

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=registry)

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(
            OpenAIChatRequest(
                messages=[{"role": "user", "content": "resume"}],
                model="gemini-3-flash",
                conversation_id="does-not-exist",
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "The provided conversation_id was not found."


@pytest.mark.asyncio
async def test_registry_tombstone_blocks_concurrent_get_session(mocker):
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    registry = SessionRegistry(MockGeminiClient(), repository=repo)
    await registry.begin_delete_session("conv-deleting")

    with pytest.raises(ConversationInUseError):
        await registry.get_session(
            "conv-deleting",
            GeminiProvider(),
            allow_create=False,
            model="gemini-3-flash",
        )

    await registry.abort_delete_session("conv-deleting")
    assert "conv-deleting" not in registry._deleting


@pytest.mark.asyncio
async def test_registry_begin_delete_rejects_active_locked_session(mocker):
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    registry = SessionRegistry(MockGeminiClient(), repository=repo)
    manager = await registry.get_session("conv-active")
    await manager.lock.acquire()
    try:
        with pytest.raises(ConversationInUseError):
            await registry.begin_delete_session("conv-active")
    finally:
        manager.lock.release()


@pytest.mark.asyncio
async def test_registry_begin_delete_rejects_active_stream_session(mocker):
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    registry = SessionRegistry(MockGeminiClient(), repository=repo)
    manager = await registry.get_session("conv-streaming")
    manager.active_streams = 1

    with pytest.raises(ConversationInUseError):
        await registry.begin_delete_session("conv-streaming")


@pytest.mark.asyncio
async def test_model_mismatch_does_not_block_recovery(mocker):
    provider = GeminiProvider()
    client = MockGeminiClient()
    saved_snapshots = []
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    registry = SessionRegistry(client, repository=repo)

    manager = await registry.get_session(
        "conv-model-switch",
        provider,
        allow_create=True,
        model="gemini-3-flash",
    )
    manager.session = MockChatSession(
        ["cid", "rid", "rcid"],
        "gemini-3-flash",
    )
    await registry.save_session_snapshot("conv-model-switch", provider, manager)
    snapshot = saved_snapshots[0]

    restored_repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=snapshot),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    restored_registry = SessionRegistry(client, repository=restored_repo)
    restored = await restored_registry.get_session(
        "conv-model-switch",
        provider,
        allow_create=False,
        model="gemini-3-pro",
    )

    assert restored.model == "gemini-3-pro"
    assert restored.session.model == "gemini-3-pro"


@pytest.mark.asyncio
async def test_file_parts_are_passed_on_current_turn_without_persisting_payload(mocker):
    provider = GeminiProvider()
    client = MockGeminiClient()
    mock_repository = SimpleNamespace(save_snapshot=mocker.AsyncMock())
    registry = SessionRegistry(client, repository=mock_repository)

    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_client", return_value=client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=registry)
    mocker.patch("app.services.providers.gemini.provider.generate_opaque_token", return_value="conv-file")

    response = await provider.chat_completions(
        OpenAIChatRequest(
            messages=[
                {"role": "user", "content": "Summarize this file."},
                {
                    "role": "user",
                    "content": [
                        {"type": "file", "file": {"filename": "invoice.pdf", "file_data": "data:application/pdf;base64,JVBERi0xLjQK"}},
                    ],
                },
            ],
            model="gemini-3-flash",
        )
    )

    assert response["conversation_id"] == "conv-file"
    assert client.sessions[-1].files_received[-1] is not None
    assert len(client.sessions[-1].files_received[-1]) == 1

    snapshot = mock_repository.save_snapshot.call_args.args[0]
    snapshot_text = json.dumps(snapshot.session_state)
    assert "file_data" not in snapshot_text
    assert "invoice.pdf" not in snapshot_text
    assert "JVBERi0xLjQK" not in snapshot_text


@pytest.mark.asyncio
async def test_registry_uses_provider_adapter_name_for_snapshot_identity(mocker):
    provider = GeminiProvider()
    client = MockGeminiClient()
    saved_snapshots = []
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    registry = SessionRegistry(client, repository=repo)

    manager = await registry.get_session(
        "conv-provider-name",
        provider,
        allow_create=True,
        model="gemini-3-flash",
    )
    manager.session = client.start_chat("gemini-3-flash")

    await registry.save_session_snapshot("conv-provider-name", provider, manager)

    snapshot = saved_snapshots[0]
    assert snapshot.provider_name == provider.provider_name


@pytest.mark.asyncio
async def test_restored_metadata_is_isolated_from_default_metadata(mocker):
    from gemini_webapi.constants import DEFAULT_METADATA

    original_default = list(DEFAULT_METADATA)
    provider = GeminiProvider()
    client = MockGeminiClient(initial_metadata_factory=lambda: DEFAULT_METADATA)
    saved_snapshots = []
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    registry = SessionRegistry(client, repository=repo)

    manager = await registry.get_session(
        "conv-metadata",
        provider,
        allow_create=True,
        model="gemini-3-flash",
    )
    manager.session = MockChatSession(
        ["cid", "rid", "rcid", None, None, None, None, None, None, "ctx"],
        "gemini-3-flash",
    )
    await registry.save_session_snapshot("conv-metadata", provider, manager)

    restored_repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=saved_snapshots[0]),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    restored_registry = SessionRegistry(client, repository=restored_repo)
    restored = await restored_registry.get_session(
        "conv-metadata",
        provider,
        allow_create=False,
        model="gemini-3-flash",
    )

    assert restored.session.metadata is not DEFAULT_METADATA
    restored.session.metadata[0] = "changed"
    assert list(DEFAULT_METADATA) == original_default


def test_validate_session_recovery_rejects_invalid_provider_version():
    provider = GeminiProvider()

    with pytest.raises(StateIntegrityError):
        provider.validate_session_recovery(
            {
                "provider_state_version": 999,
                "metadata": ["cid", "rid", "rcid", None, None, None, None, None, None, "ctx"],
                "gem_id": None,
                "model_name": "gemini-3-flash",
            },
            {},
        )


def test_validate_session_recovery_rejects_non_dict_payload():
    provider = GeminiProvider()

    with pytest.raises(StateIntegrityError):
        provider.validate_session_recovery("not-a-dict", {})


def test_validate_session_recovery_rejects_missing_required_fields():
    provider = GeminiProvider()

    with pytest.raises(StateIntegrityError):
        provider.validate_session_recovery(
            {
                "provider_state_version": 1,
                "metadata": ["cid"],
                "model_name": "gemini-3-flash",
            },
            {},
        )
