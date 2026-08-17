import asyncio
import json
from datetime import datetime, timezone

import pytest
from types import SimpleNamespace
from fastapi import HTTPException

from app.schemas.request import OpenAIChatRequest
from app.services.providers.exceptions import (
    ConversationInUseError,
    SnapshotNotFoundError,
    StateIntegrityError,
)
from app.services.providers.base_repository import ConversationSnapshot
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.sqlite_repository import SQLiteConversationRepository
import app.services.providers.gemini.client as gemini_client_module
import app.services.providers.gemini.session_manager as session_manager_module
from app.services.providers.gemini.session_manager import (
    SNAPSHOT_SCHEMA_VERSION,
    SessionManager,
    SessionRegistry,
)


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

    def resolve_model(self, model_name):
        return SimpleNamespace(model_name=model_name, is_available=True)


def _conversation_snapshot(conversation_id):
    return ConversationSnapshot(
        conversation_id=conversation_id,
        provider_name="gemini",
        session_state={
            "provider_state_version": 1,
            "metadata": ["cid", "rid", "rcid"],
            "gem_id": None,
            "model_name": "gemini-3-flash",
        },
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_session_manager_get_response_passes_temporary_flag(mocker):
    mock_session = mocker.Mock()
    mock_session.send_message = mocker.AsyncMock(return_value=MockResponse("ok"))

    mock_client = mocker.Mock()
    mock_client.start_chat = mocker.Mock(return_value=mock_session)

    manager = SessionManager(mock_client, client_generation=0)

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
async def test_restart_recovery_reuses_snapshot_and_sends_only_final_message(mocker, install_gemini_client, install_registry_generation):
    provider = GeminiProvider()
    client = MockGeminiClient()
    saved_snapshots = []
    first_repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_gemini_client(client)
    first_registry = SessionRegistry(
        client,
        repository=first_repo,
        generation=generation,
    )
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
    second_registry = SessionRegistry(
        client,
        repository=second_repo,
        generation=generation,
    )
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
async def test_restart_recovery_reuses_snapshot_and_passes_file_on_current_turn(mocker, install_gemini_client, install_registry_generation):
    provider = GeminiProvider()
    client = MockGeminiClient()
    saved_snapshots = []
    first_repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_gemini_client(client)
    first_registry = SessionRegistry(
        client,
        repository=first_repo,
        generation=generation,
    )
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
    second_registry = SessionRegistry(
        client,
        repository=second_repo,
        generation=generation,
    )
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
async def test_registry_fails_closed_when_requested_snapshot_is_missing(mocker, install_registry_generation):
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)

    with pytest.raises(SnapshotNotFoundError):
        await registry.get_session(
            "missing-conversation",
            GeminiProvider(),
            allow_create=False,
            model="gemini-3-flash",
        )


@pytest.mark.asyncio
async def test_provider_returns_recovery_error_for_missing_snapshot(mocker, install_gemini_client, install_registry_generation):
    provider = GeminiProvider()
    client = MockGeminiClient()
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_gemini_client(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
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
async def test_registry_tombstone_blocks_concurrent_get_session(mocker, install_registry_generation):
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
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
async def test_slow_restore_does_not_block_unrelated_lookup_or_create(mocker, install_registry_generation):
    started = asyncio.Event()
    release = asyncio.Event()
    snapshot = _conversation_snapshot("restore-a")

    async def get_snapshot(conversation_id):
        started.set()
        await release.wait()
        return snapshot

    repo = SimpleNamespace(
        get_snapshot=get_snapshot,
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    existing = await registry.get_session("existing-b")

    restore_task = asyncio.create_task(
        registry.get_session("restore-a", GeminiProvider(), allow_create=False)
    )
    await started.wait()

    lookup_done = asyncio.Event()
    create_done = asyncio.Event()

    async def lookup():
        assert await registry.get_session("existing-b") is existing
        lookup_done.set()

    async def create():
        await registry.get_session("new-c")
        create_done.set()

    await asyncio.gather(lookup(), create())
    assert lookup_done.is_set()
    assert create_done.is_set()

    release.set()
    await restore_task


@pytest.mark.asyncio
async def test_concurrent_same_id_restores_share_one_published_manager(mocker, install_registry_generation):
    started = asyncio.Event()
    release = asyncio.Event()
    snapshot = _conversation_snapshot("shared")

    async def get_snapshot(conversation_id):
        started.set()
        await release.wait()
        return snapshot

    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=get_snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    provider = GeminiProvider()

    first = asyncio.create_task(
        registry.get_session("shared", provider, allow_create=False)
    )
    await started.wait()
    second = asyncio.create_task(
        registry.get_session("shared", provider, allow_create=False)
    )
    release.set()

    first_manager, second_manager = await asyncio.gather(first, second)
    assert first_manager is second_manager
    assert registry._sessions["shared"] is first_manager
    repo.get_snapshot.assert_awaited_once_with("shared")


@pytest.mark.asyncio
async def test_restore_vs_delete_does_not_resurrect_conversation(mocker, install_registry_generation):
    started = asyncio.Event()
    release = asyncio.Event()
    snapshot = _conversation_snapshot("deleted")

    async def get_snapshot(conversation_id):
        started.set()
        await release.wait()
        return snapshot

    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=get_snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    restore_task = asyncio.create_task(
        registry.get_session("deleted", GeminiProvider(), allow_create=False)
    )
    await started.wait()

    await registry.begin_delete_session("deleted")
    release.set()
    with pytest.raises(ConversationInUseError):
        await restore_task

    assert "deleted" not in registry._sessions
    await registry.complete_delete_session("deleted")


@pytest.mark.asyncio
async def test_restore_does_not_overwrite_concurrent_new_session(mocker, install_registry_generation):
    started = asyncio.Event()
    release = asyncio.Event()
    snapshot = _conversation_snapshot("race")

    async def get_snapshot(conversation_id):
        started.set()
        await release.wait()
        return snapshot

    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=get_snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    provider = GeminiProvider()
    restore_task = asyncio.create_task(
        registry.get_session("race", provider, allow_create=False)
    )
    await started.wait()

    created = await registry.get_session("race", allow_create=True)
    release.set()
    restored_result = await restore_task

    assert restored_result is created
    assert list(registry._sessions).count("race") == 1


@pytest.mark.asyncio
async def test_restore_retries_after_client_generation_replacement(mocker, install_registry_generation):
    started = asyncio.Event()
    release = asyncio.Event()
    snapshot = _conversation_snapshot("generation")
    calls = 0

    async def get_snapshot(conversation_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return snapshot

    client1 = MockGeminiClient()
    client2 = MockGeminiClient()
    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=get_snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_registry_generation(client1)
    new_generation = install_registry_generation(client2, generation=generation + 1)
    registry = SessionRegistry(client1, repository=repo, generation=generation)
    restore_task = asyncio.create_task(
        registry.get_session("generation", GeminiProvider(), allow_create=False)
    )
    await started.wait()

    await registry.update_client(client2, generation=new_generation)
    release.set()
    manager = await restore_task

    assert manager.client is client2
    assert manager.client_generation == registry.client_generation
    assert manager.session_generation == registry.client_generation
    assert calls == 2


@pytest.mark.asyncio
async def test_restore_lease_defers_retired_client_close_until_retry_finishes(mocker, install_registry_generation):
    started = asyncio.Event()
    release = asyncio.Event()
    snapshot = _conversation_snapshot("leased-generation")
    client1 = MockGeminiClient()
    client2 = MockGeminiClient()
    client1.close = mocker.AsyncMock()
    client2.close = mocker.AsyncMock()

    async def get_snapshot(conversation_id):
        started.set()
        await release.wait()
        return snapshot

    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=get_snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_registry_generation(client1)
    new_generation = install_registry_generation(client2, generation=generation + 1)
    registry = SessionRegistry(client1, repository=repo, generation=generation)
    restore_task = asyncio.create_task(
        registry.get_session("leased-generation", GeminiProvider(), allow_create=False)
    )
    await started.wait()

    old_record = gemini_client_module._gemini_generation_records[registry.client_generation]
    assert old_record.lease_count == 1
    gemini_client_module._retire_generation(old_record)
    await gemini_client_module._close_generation_record(old_record)
    client1.close.assert_not_awaited()

    await registry.update_client(client2, generation=new_generation)
    release.set()
    manager = await restore_task

    assert manager.client is client2
    assert manager.client_generation == registry.client_generation
    assert old_record.lease_count == 0
    client1.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_restore_retries_when_captured_generation_retires_before_lease(mocker, monkeypatch, install_registry_generation):
    snapshot = _conversation_snapshot("lease-race")
    client1 = MockGeminiClient()
    client2 = MockGeminiClient()
    old_generation = install_registry_generation(client1)
    registry = SessionRegistry(client1, repository=SimpleNamespace(
        get_snapshot=mocker.AsyncMock(return_value=snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    ), generation=old_generation)
    provider = GeminiProvider()
    old_record = gemini_client_module._gemini_generation_records[old_generation]
    calls = 0
    original_acquire = session_manager_module.acquire_gemini_lease

    def acquire_with_retirement(*, client, generation):
        nonlocal calls
        calls += 1
        if calls == 1:
            gemini_client_module._retire_generation(old_record)
            new_generation = install_registry_generation(client2, generation=old_generation + 1)
            registry.client = client2
            registry.client_generation = new_generation
            raise gemini_client_module.GeminiGenerationUnavailableError(
                "Gemini client generation is retired."
            )
        return original_acquire(client=client, generation=generation)

    monkeypatch.setattr(session_manager_module, "acquire_gemini_lease", acquire_with_retirement)
    manager = await registry.get_session(
        "lease-race", provider, allow_create=False
    )

    assert calls == 2
    assert manager.client is client2
    assert manager.client_generation == registry.client_generation


@pytest.mark.asyncio
async def test_restore_failure_releases_attempt_lease(mocker, install_registry_generation):
    client = MockGeminiClient()
    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=RuntimeError("restore failed")),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)

    with pytest.raises(RuntimeError, match="restore failed"):
        await registry.get_session("restore-failure", GeminiProvider(), allow_create=False)

    record = gemini_client_module._gemini_generation_records[registry.client_generation]
    assert record.lease_count == 0
    assert "restore-failure" not in registry._restore_tasks


@pytest.mark.asyncio
async def test_failed_restore_clears_inflight_state_and_allows_retry(mocker, install_registry_generation):
    snapshot = _conversation_snapshot("retry")
    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(
            side_effect=[RuntimeError("repository unavailable"), snapshot]
        ),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    provider = GeminiProvider()

    with pytest.raises(RuntimeError, match="repository unavailable"):
        await registry.get_session("retry", provider, allow_create=False)
    assert "retry" not in registry._restore_tasks

    manager = await registry.get_session("retry", provider, allow_create=False)
    assert registry._sessions["retry"] is manager
    assert repo.get_snapshot.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_restore_waiter_does_not_cancel_shared_restore(mocker, install_registry_generation):
    started = asyncio.Event()
    release = asyncio.Event()
    snapshot = _conversation_snapshot("cancel")

    async def get_snapshot(conversation_id):
        started.set()
        await release.wait()
        return snapshot

    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=get_snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    provider = GeminiProvider()
    owner = asyncio.create_task(
        registry.get_session("cancel", provider, allow_create=False)
    )
    await started.wait()
    waiter = asyncio.create_task(
        registry.get_session("cancel", provider, allow_create=False)
    )
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert not owner.done()
    release.set()
    manager = await owner
    assert registry._sessions["cancel"] is manager
    assert "cancel" not in registry._restore_tasks


@pytest.mark.asyncio
async def test_registry_shutdown_cancels_restore_and_releases_lease(mocker, install_registry_generation):
    started = asyncio.Event()
    release = asyncio.Event()
    client = MockGeminiClient()
    client.close = mocker.AsyncMock()

    async def get_snapshot(conversation_id):
        started.set()
        await release.wait()
        return _conversation_snapshot(conversation_id)

    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=get_snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    owner = asyncio.create_task(
        registry.get_session("shutdown", GeminiProvider(), allow_create=False)
    )
    await started.wait()

    record = gemini_client_module._gemini_generation_records[registry.client_generation]
    assert record.lease_count == 1
    await registry.shutdown()

    with pytest.raises(asyncio.CancelledError):
        await owner
    assert record.lease_count == 0
    assert registry._restore_tasks == {}
    assert "shutdown" not in registry._sessions

    await gemini_client_module.close_gemini_client()
    client.close.assert_awaited_once_with()
    await registry.shutdown()


@pytest.mark.asyncio
async def test_closed_registry_rejects_new_and_existing_session_access(mocker, install_registry_generation):
    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    await registry.get_session("existing")
    await registry.shutdown()

    with pytest.raises(RuntimeError, match="Session registry is closed"):
        await registry.get_session("existing")
    with pytest.raises(RuntimeError, match="Session registry is closed"):
        await registry.get_session("new", GeminiProvider(), allow_create=False)
    assert repo.get_snapshot.await_count == 0


@pytest.mark.asyncio
async def test_closed_restore_candidate_cannot_publish(mocker, install_registry_generation):
    ready = asyncio.Event()
    release = asyncio.Event()
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(
        client,
        repository=SimpleNamespace(
            get_snapshot=mocker.AsyncMock(return_value=_conversation_snapshot("guard")),
            save_snapshot=mocker.AsyncMock(),
            delete_snapshot=mocker.AsyncMock(),
            list_snapshots=mocker.AsyncMock(return_value=[]),
        ),
        generation=generation,
    )
    original_restore = registry._restore_session

    async def paused_restore(*args, **kwargs):
        manager = await original_restore(*args, **kwargs)
        ready.set()
        await release.wait()
        return manager

    mocker.patch.object(registry, "_restore_session", side_effect=paused_restore)
    owner = asyncio.create_task(
        registry.get_session("guard", GeminiProvider(), allow_create=False)
    )
    await ready.wait()
    async with registry._lock:
        registry._closed = True
    release.set()

    with pytest.raises(RuntimeError, match="Session registry is closed"):
        await owner
    assert "guard" not in registry._sessions
    assert registry._restore_tasks == {}


@pytest.mark.asyncio
async def test_registry_reopen_preserves_sessions_and_allows_new_restore(mocker, install_registry_generation):
    old_client = MockGeminiClient()
    new_client = MockGeminiClient()
    snapshot = _conversation_snapshot("after-reopen")
    started = asyncio.Event()
    release = asyncio.Event()

    async def get_snapshot(conversation_id):
        if conversation_id == "old-restore":
            started.set()
            await release.wait()
        return snapshot

    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(side_effect=get_snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    old_generation = install_registry_generation(old_client)
    registry = SessionRegistry(
        old_client,
        repository=repo,
        generation=old_generation,
    )
    existing = await registry.get_session("existing")
    new_generation = install_registry_generation(new_client, generation=old_generation + 1)
    old_owner = asyncio.create_task(
        registry.get_session("old-restore", GeminiProvider(), allow_create=False)
    )
    await started.wait()

    async with registry._lock:
        registry._closed = True
    with pytest.raises(RuntimeError, match="pending restore tasks"):
        await registry.reopen(old_client, generation=registry.client_generation)
    registry._closed = False

    await registry.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await old_owner
    await registry.reopen(new_client, generation=new_generation)

    assert registry._sessions["existing"] is existing
    assert existing.client is new_client
    assert existing.client_generation == new_generation
    restored = await registry.get_session(
        "after-reopen", GeminiProvider(), allow_create=False
    )
    assert restored.client is new_client
    assert restored.client_generation == new_generation
    assert registry._restore_tasks == {}


@pytest.mark.asyncio
async def test_restore_at_capacity_preserves_prune_then_publish_behavior(mocker, monkeypatch, install_registry_generation):
    import app.services.providers.gemini.session_manager as session_manager_module

    monkeypatch.setattr(session_manager_module, "MAX_SESSIONS", 1)
    repo = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(return_value=_conversation_snapshot("restore")),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    await registry.get_session("old")

    restored = await registry.get_session(
        "restore", GeminiProvider(), allow_create=False
    )

    assert registry._sessions["restore"] is restored
    assert len(registry._sessions) == 1


@pytest.mark.asyncio
async def test_registry_begin_delete_rejects_active_locked_session(mocker, install_registry_generation):
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    manager = await registry.get_session("conv-active")
    await manager.lock.acquire()
    try:
        with pytest.raises(ConversationInUseError):
            await registry.begin_delete_session("conv-active")
    finally:
        manager.lock.release()


@pytest.mark.asyncio
async def test_registry_begin_delete_rejects_active_stream_session(mocker, install_registry_generation):
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    client = MockGeminiClient()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)
    manager = await registry.get_session("conv-streaming")
    manager.active_streams = 1

    with pytest.raises(ConversationInUseError):
        await registry.begin_delete_session("conv-streaming")


@pytest.mark.asyncio
async def test_model_mismatch_does_not_block_recovery(mocker, install_registry_generation):
    provider = GeminiProvider()
    client = MockGeminiClient()
    saved_snapshots = []
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)

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
    restored_registry = SessionRegistry(
        client,
        repository=restored_repo,
        generation=generation,
    )
    restored = await restored_registry.get_session(
        "conv-model-switch",
        provider,
        allow_create=False,
        model="gemini-3-pro",
    )

    assert restored.model == "gemini-3-pro"
    assert restored.session.model == "gemini-3-pro"


@pytest.mark.asyncio
async def test_file_parts_are_passed_on_current_turn_without_persisting_payload(mocker, install_gemini_client):
    provider = GeminiProvider()
    client = MockGeminiClient()
    mock_repository = SimpleNamespace(save_snapshot=mocker.AsyncMock())
    generation = install_gemini_client(client)
    registry = SessionRegistry(
        client,
        repository=mock_repository,
        generation=generation,
    )
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
async def test_registry_uses_provider_adapter_name_for_snapshot_identity(mocker, install_registry_generation):
    provider = GeminiProvider()
    client = MockGeminiClient()
    saved_snapshots = []
    repo = SimpleNamespace(
        save_snapshot=mocker.AsyncMock(side_effect=lambda snapshot: saved_snapshots.append(snapshot)),
        get_snapshot=mocker.AsyncMock(return_value=None),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)

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
async def test_restored_metadata_is_isolated_from_default_metadata(mocker, install_registry_generation):
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
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, repository=repo, generation=generation)

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
    restored_registry = SessionRegistry(
        client,
        repository=restored_repo,
        generation=generation,
    )
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
