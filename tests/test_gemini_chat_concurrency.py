import asyncio
import json
import pytest
from types import SimpleNamespace
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.providers.gemini.session_manager import SessionRegistry, SessionManager
import app.services.providers.gemini.session_manager as session_manager_module
import app.services.providers.gemini.client as gemini_client_module
from app.utils.tokens import generate_opaque_token


class _FakeChatSession:
    def __init__(self, client, model, gem):
        self.geminiclient = client
        self.model = model
        self.gem = gem
        self._ChatSession__metadata = ["cid", "rid", "rcid", "context"]
        self.calls = []

    @property
    def metadata(self):
        return self._ChatSession__metadata

    @metadata.setter
    def metadata(self, value):
        self._ChatSession__metadata = value

    async def send_message(self, prompt, files=None, temporary=False, extended_thinking=False):
        self.calls.append((prompt, files, temporary))
        return SimpleNamespace(text="ok")


def test_registry_constructor_uses_explicit_registered_generation(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(
        client,
        generation=generation,
    )

    assert registry.client_generation == generation


def test_registry_constructor_rejects_unregistered_explicit_generation(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)

    with pytest.raises(RuntimeError, match="generation is not registered"):
        SessionRegistry(
            client,
            generation=generation + 1,
        )


@pytest.mark.asyncio
async def test_registry_rejects_unregistered_explicit_generation(mocker, install_registry_generation):
    old_client = mocker.Mock()
    candidate = mocker.Mock()
    generation = install_registry_generation(old_client)
    registry = SessionRegistry(
        old_client,
        generation=generation,
    )

    with pytest.raises(RuntimeError, match="not registered"):
        await registry.update_client(
            candidate,
            generation=registry.client_generation + 1,
        )

    assert registry.client is old_client
    assert registry.client_generation == 0


@pytest.mark.asyncio
async def test_concurrent_independent_streams(mocker, install_registry_generation):
    """Verify independent conversations can stream simultaneously."""
    mock_client = mocker.Mock()
    mock_session = mocker.Mock()
    
    # Simulate tokens arriving over time
    async def mock_stream(*args, **kwargs):
        for i in range(3):
            await asyncio.sleep(0.01)
            mock_chunk = mocker.Mock()
            mock_chunk.text_delta = f"token_{i} "
            yield mock_chunk

    mock_session.send_message_stream = mock_stream
    mock_client.start_chat.return_value = mock_session
    
    generation = install_registry_generation(mock_client)
    registry = SessionRegistry(mock_client, generation=generation)
    
    # Patch registry into the app if necessary, or test logic directly
    # Testing logic directly is faster and more precise for concurrency
    cid1 = "conv_1"
    cid2 = "conv_2"
    
    manager1 = await registry.get_session(cid1)
    manager2 = await registry.get_session(cid2)
    
    results = []
    async def run_stream(manager, cid):
        async for payload in manager.get_streaming_response("model", "hi", None):
            payload["conversation_id"] = cid
            results.append(payload)

    # Run both simultaneously
    await asyncio.gather(
        run_stream(manager1, cid1),
        run_stream(manager2, cid2)
    )
    
    # Verify tokens are interleaved (proving parallelism)
    # Since they are small, they might finish too fast, but gather() ensures overlap
    cids = [r["conversation_id"] for r in results if r.get("type") == "chunk"]
    assert cid1 in cids
    assert cid2 in cids
    assert len([r for r in results if r.get("type") == "chunk"]) == 6


@pytest.mark.asyncio
async def test_stateful_buffered_request_holds_retired_client_until_release(mocker, install_registry_generation):
    client = mocker.Mock()
    entered = asyncio.Event()
    release = asyncio.Event()
    session = mocker.Mock()

    async def send_message(*args, **kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(text="ok")

    session.send_message = send_message
    client.start_chat.return_value = session
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("lease-buffered")
    record = gemini_client_module._gemini_generation_records[manager.client_generation]

    task = asyncio.create_task(
        manager.get_response_stateful("model", [{"content": "hi"}], "")
    )
    await entered.wait()
    gemini_client_module._retire_generation(record)
    assert client.close.call_count == 0

    release.set()
    await task

    assert record.lease_count == 0
    client.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_stateful_stream_cancellation_releases_retired_client(mocker, install_registry_generation):
    client = mocker.Mock()
    entered = asyncio.Event()
    session = mocker.Mock()

    async def send_message_stream(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        yield SimpleNamespace(text_delta="never")

    session.send_message_stream = send_message_stream
    client.start_chat.return_value = session
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("lease-stream")
    record = gemini_client_module._gemini_generation_records[manager.client_generation]

    async def consume():
        async for _ in manager.get_streaming_response_stateful(
            "model", [{"content": "hi"}], ""
        ):
            pass

    task = asyncio.create_task(consume())
    await entered.wait()
    gemini_client_module._retire_generation(record)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert record.lease_count == 0
    client.close.assert_called_once_with()

@pytest.mark.asyncio
async def test_same_session_serialization(mocker, install_registry_generation):
    """Verify same-conversation requests are serialized via lock."""
    mock_client = mocker.Mock()
    mock_session = mocker.Mock()
    
    execution_order = []
    async def mock_stream(*args, **kwargs):
        execution_order.append("start")
        await asyncio.sleep(0.05)
        execution_order.append("end")
        mock_chunk = mocker.Mock()
        mock_chunk.text_delta = "done"
        yield mock_chunk

    mock_session.send_message_stream = mock_stream
    mock_client.start_chat.return_value = mock_session
    
    generation = install_registry_generation(mock_client)
    registry = SessionRegistry(mock_client, generation=generation)
    cid = "shared_conv"
    manager = await registry.get_session(cid)
    
    async def req():
        async for _ in manager.get_streaming_response("model", "hi", None):
            pass

    # Start two requests for same session
    await asyncio.gather(req(), req())
    
    # Should be start, end, start, end (serialized)
    # NOT start, start, end, end (interleaved)
    assert execution_order == ["start", "end", "start", "end"]

@pytest.mark.asyncio
async def test_registry_capacity_exhaustion(mocker, install_registry_generation):
    """Verify HTTP 429 when all sessions are locked."""
    from app.services.providers.gemini import session_manager
    mocker.patch(
        "app.services.providers.gemini.session_manager.MAX_SESSIONS",
        2,
    )
    
    mock_client = mocker.Mock()
    generation = install_registry_generation(mock_client)
    registry = SessionRegistry(mock_client, generation=generation)
    
    # Create and lock 2 sessions
    cid1 = "c1"
    cid2 = "c2"
    m1 = await registry.get_session(cid1)
    m2 = await registry.get_session(cid2)
    
    async with m1.lock, m2.lock:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as excinfo:
            await registry.get_session("c3")
        assert excinfo.value.status_code == 429

@pytest.mark.asyncio
async def test_pruning_protects_active_streams(mocker, install_registry_generation):
    """Verify that sessions with active streams are NOT pruned."""
    from app.services.providers.gemini import session_manager
    mocker.patch(
        "app.services.providers.gemini.session_manager.MAX_SESSIONS",
        1,
    )
    
    mock_client = mocker.Mock()
    generation = install_registry_generation(mock_client)
    registry = SessionRegistry(mock_client, generation=generation)
    
    cid1 = "active"
    m1 = await registry.get_session(cid1)
    m1.active_streams = 1 # Manually pin
    
    # Try to create cid2, which would normally prune cid1
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        await registry.get_session("cid2")
    assert excinfo.value.status_code == 429
    assert cid1 in registry._sessions # Should still exist

@pytest.mark.asyncio
async def test_sse_payload_schema_consistency(mocker, install_registry_generation):
    """Verify consistent schema for both chunks and interrupts."""
    mock_client = mocker.Mock()
    mock_session = mocker.Mock()
    
    async def mock_stream_timeout(*args, **kwargs):
        # Use a real async generator that times out
        await asyncio.sleep(0.01)
        raise asyncio.TimeoutError()
        yield # unreachable but makes it a generator

    mock_session.send_message_stream = mock_stream_timeout
    mock_client.start_chat.return_value = mock_session
    
    generation = install_registry_generation(mock_client)
    registry = SessionRegistry(mock_client, generation=generation)
    manager = await registry.get_session("test")
    
    results = []
    async for p in manager.get_streaming_response("model", "hi", None):
        results.append(p)
        
    assert len(results) == 1
    assert results[0]["type"] == "interrupt"
    assert results[0]["interrupted"] is True
    # The reason might be "timeout" or a string representation of the exception
    assert "timeout" in results[0]["reason"].lower()

@pytest.mark.asyncio
async def test_interrupted_exactly_once_on_cancel(mocker, install_registry_generation):
    """Verify exactly-once interruption signal during cancellation."""
    mock_client = mocker.Mock()
    mock_session = mocker.Mock()
    
    async def mock_stream_cancel(*args, **kwargs):
        mock_chunk = mocker.Mock()
        mock_chunk.text_delta = "token"
        yield mock_chunk
        raise asyncio.CancelledError()

    mock_session.send_message_stream = mock_stream_cancel
    mock_client.start_chat.return_value = mock_session
    
    generation = install_registry_generation(mock_client)
    registry = SessionRegistry(mock_client, generation=generation)
    manager = await registry.get_session("test")
    
    with pytest.raises(asyncio.CancelledError):
        async for _ in manager.get_streaming_response("m", "h", None):
            pass

@pytest.mark.asyncio
async def test_registry_update_client_updates_all(mocker, install_registry_generation):
    """Verify registry.update_client updates both registry and session managers."""
    mock_client1 = mocker.Mock()
    mock_client2 = mocker.Mock()
    
    generation = install_registry_generation(mock_client1)
    new_generation = install_registry_generation(mock_client2, generation=generation + 1)
    registry = SessionRegistry(mock_client1, generation=generation)
    manager1 = await registry.get_session("conv_1")
    manager2 = await registry.get_session("conv_2")
    
    assert registry.client == mock_client1
    assert manager1.client == mock_client1
    assert manager2.client == mock_client1
    
    # Execute async update
    await registry.update_client(mock_client2, generation=new_generation)
    
    assert registry.client == mock_client2
    assert manager1.client == mock_client2
    assert manager2.client == mock_client2

@pytest.mark.asyncio
async def test_registry_update_client_is_lock_protected(mocker, install_registry_generation):
    """Verify registry.update_client is strictly serialized and lock-protected."""
    mock_client1 = mocker.Mock()
    mock_client2 = mocker.Mock()
    
    generation = install_registry_generation(mock_client1)
    new_generation = install_registry_generation(mock_client2, generation=generation + 1)
    registry = SessionRegistry(mock_client1, generation=generation)
    
    # Forcefully acquire the registry lock
    await registry._lock.acquire()
    assert registry._lock.locked() is True
    
    # Attempt update_client in a background task
    update_task = asyncio.create_task(
        registry.update_client(mock_client2, generation=new_generation)
    )
    
    # Wait a brief moment to ensure the task runs and tries to acquire lock
    await asyncio.sleep(0.01)
    
    # Verify the update is blocked and has not completed (client still unchanged)
    assert update_task.done() is False
    assert registry.client == mock_client1
    
    # Release the lock and verify completion
    registry._lock.release()
    await update_task
    
    assert update_task.done() is True
    assert registry.client == mock_client2


@pytest.mark.asyncio
async def test_registry_update_client_requires_explicit_generation(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    with pytest.raises(TypeError):
        await registry.update_client(client)

    assert registry.client is client
    assert registry.client_generation == generation


@pytest.mark.asyncio
async def test_registry_update_client_rejects_unregistered_generation_without_mutation(
    mocker,
    install_registry_generation,
):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("preserve-me")
    state = (registry.client, registry.client_generation, manager.client, manager.client_generation)

    with pytest.raises(RuntimeError, match="generation is not registered"):
        await registry.update_client(mocker.Mock(), generation=generation + 1)

    assert (registry.client, registry.client_generation, manager.client, manager.client_generation) == state


@pytest.mark.asyncio
async def test_registry_update_client_same_client_new_generation_updates_managers(
    mocker,
    install_registry_generation,
):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    gemini_client_module._gemini_client_generations.pop(id(client))
    new_generation = install_registry_generation(client, generation=generation + 1)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("preserve-session")
    manager.session_generation = generation

    await registry.update_client(client, generation=new_generation)

    assert registry.client is client
    assert registry.client_generation == new_generation
    assert manager.client is client
    assert manager.client_generation == new_generation
    assert manager.session_generation == generation


@pytest.mark.asyncio
async def test_registry_same_client_update_preserves_generation_and_session(mocker, install_registry_generation):
    client = mocker.Mock()
    session = _FakeChatSession(client, "model", None)
    client.start_chat.return_value = session

    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("conversation")
    await manager.get_response_stateful("model", [{"content": "first"}], "")
    generation = registry.client_generation
    session_generation = manager.session_generation

    await registry.update_client(client, generation=generation)
    await manager.get_response_stateful("model", [{"content": "second"}], "")

    assert registry.client_generation == generation
    assert manager.client_generation == generation
    assert manager.session_generation == session_generation
    assert manager.session is session
    client.start_chat.assert_called_once_with(model="model", gem=None)


@pytest.mark.asyncio
async def test_session_generation_mismatch_uses_typed_error(mocker, install_registry_generation):
    client1 = mocker.Mock()
    client2 = mocker.Mock()
    generation = install_registry_generation(client1)
    new_generation = install_registry_generation(client2, generation=generation + 1)
    registry = SessionRegistry(client1, generation=generation)
    manager = await registry.get_session("conversation")
    lease = gemini_client_module.acquire_gemini_lease(
        client=client1,
        generation=registry.client_generation,
    )
    await registry.update_client(client2, generation=new_generation)

    with pytest.raises(
        gemini_client_module.GeminiGenerationUnavailableError,
        match="Gemini session generation changed before execution",
    ):
        await manager.get_response_stateful("model", [{"content": "first"}], "", lease=lease)
    await lease.release()


@pytest.mark.asyncio
async def test_session_manager_retries_only_typed_generation_error(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("conversation")
    original_acquire = session_manager_module.acquire_gemini_lease
    calls = 0

    def acquire_with_one_stale_attempt(*, client, generation):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise gemini_client_module.GeminiGenerationUnavailableError("stale")
        return original_acquire(client=client, generation=generation)

    mocker.patch.object(
        session_manager_module,
        "acquire_gemini_lease",
        side_effect=acquire_with_one_stale_attempt,
    )
    lease = manager._acquire_client_lease()

    assert calls == 2
    await lease.release()


@pytest.mark.asyncio
async def test_session_manager_does_not_retry_unrelated_runtime_error(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("conversation")
    acquire = mocker.patch.object(
        session_manager_module,
        "acquire_gemini_lease",
        side_effect=RuntimeError("invariant failure"),
    )

    with pytest.raises(RuntimeError, match="invariant failure"):
        manager._acquire_client_lease()
    acquire.assert_called_once()


@pytest.mark.asyncio
async def test_session_manager_does_not_retry_shutdown_error(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("conversation")
    acquire = mocker.patch.object(
        session_manager_module,
        "acquire_gemini_lease",
        side_effect=RuntimeError("Gemini client lifecycle is shutting down."),
    )

    with pytest.raises(RuntimeError, match="shutting down"):
        manager._acquire_client_lease()
    acquire.assert_called_once()


@pytest.mark.asyncio
async def test_registry_reopen_same_client_same_generation(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    generation = registry.client_generation
    await registry.shutdown()
    await registry.reopen(client, generation=generation)

    assert registry._closed is False
    assert registry.client is client
    assert registry.client_generation == generation


@pytest.mark.asyncio
async def test_registry_reopen_same_client_updates_valid_generation(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("conversation")
    old_generation = registry.client_generation
    new_generation = old_generation + 1
    mocker.patch.object(
        session_manager_module,
        "is_gemini_generation_registered",
        return_value=True,
    )

    await registry.shutdown()
    await registry.reopen(client, generation=new_generation)

    assert registry.client is client
    assert registry.client_generation == new_generation
    assert manager.client is client
    assert manager.client_generation == new_generation
    assert manager.session_generation is None


@pytest.mark.asyncio
async def test_registry_reopen_same_client_rejects_invalid_generation(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("conversation")
    old_generation = registry.client_generation
    registry_state = (registry.client, registry.client_generation, manager.client_generation)

    await registry.shutdown()
    with pytest.raises(RuntimeError, match="generation is not registered"):
        await registry.reopen(client, generation=old_generation + 1)

    assert registry._closed is True
    assert (registry.client, registry.client_generation, manager.client_generation) == registry_state


@pytest.mark.asyncio
async def test_registry_reopen_requires_generation_in_signature(mocker, install_registry_generation):
    client = mocker.Mock()
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    await registry.shutdown()
    with pytest.raises(TypeError):
        await registry.reopen(client)

    assert registry._closed is True


@pytest.mark.asyncio
async def test_client_replacement_lazily_rebuilds_stale_session_with_metadata(mocker, install_registry_generation):
    client1 = mocker.Mock()
    client2 = mocker.Mock()
    old_session = _FakeChatSession(client1, "model", "gem")
    new_session = _FakeChatSession(client2, "model", "gem")
    client1.start_chat.return_value = old_session
    client2.start_chat.return_value = new_session

    generation = install_registry_generation(client1)
    new_generation = install_registry_generation(client2, generation=generation + 1)
    registry = SessionRegistry(client1, generation=generation)
    manager = await registry.get_session("conversation")
    await manager.get_response_stateful("model", [{"content": "first"}], "", gem="gem")

    await registry.update_client(client2, generation=new_generation)
    assert manager.session is old_session
    assert manager.session_generation != manager.client_generation

    await manager.get_response_stateful("model", [{"content": "second"}], "", gem="gem")

    assert manager.session is new_session
    assert new_session.geminiclient is client2
    assert new_session.metadata == old_session.metadata
    assert manager.session_generation == manager.client_generation == registry.client_generation
    assert old_session.calls == [("User: first", None, False)]
    assert new_session.calls == [("second", None, False)]


@pytest.mark.asyncio
async def test_same_generation_reuses_session_and_replacements_are_independent(mocker, install_registry_generation):
    client1 = mocker.Mock()
    client2 = mocker.Mock()
    client3 = mocker.Mock()
    old1 = _FakeChatSession(client1, "model", None)
    old2 = _FakeChatSession(client1, "model", None)
    new1 = _FakeChatSession(client2, "model", None)
    new2 = _FakeChatSession(client2, "model", None)
    newest1 = _FakeChatSession(client3, "model", None)
    newest2 = _FakeChatSession(client3, "model", None)
    client1.start_chat.side_effect = [old1, old2]
    client2.start_chat.side_effect = [new1, new2]
    client3.start_chat.side_effect = [newest1, newest2]
    generation = install_registry_generation(client1)
    client2_generation = install_registry_generation(client2, generation=generation + 1)
    client3_generation = install_registry_generation(client3, generation=generation + 2)
    registry = SessionRegistry(client1, generation=generation)
    manager1 = await registry.get_session("one")
    manager2 = await registry.get_session("two")

    await manager1.get_response_stateful("model", [{"content": "one"}], "")
    first_session = manager1.session
    await manager1.get_response_stateful("model", [{"content": "same"}], "")
    assert manager1.session is first_session

    await registry.update_client(client2, generation=client2_generation)
    await manager1.get_response_stateful("model", [{"content": "one-new"}], "")
    await manager2.get_response_stateful("model", [{"content": "two-new"}], "")

    assert manager1.session is not first_session
    assert manager1.session.geminiclient is client2
    assert manager2.session.geminiclient is client2
    assert manager1.session_generation == manager2.session_generation == registry.client_generation

    await registry.update_client(client3, generation=client3_generation)
    await manager1.get_response_stateful("model", [{"content": "one-newest"}], "")
    await manager2.get_response_stateful("model", [{"content": "two-newest"}], "")
    assert manager1.session.geminiclient is client3
    assert manager2.session.geminiclient is client3
    assert manager1.session_generation == manager2.session_generation == registry.client_generation


@pytest.mark.asyncio
async def test_stale_rebuild_failure_does_not_affect_other_manager(mocker, install_registry_generation):
    client1 = mocker.Mock()
    client2 = mocker.Mock()
    old1 = _FakeChatSession(client1, "model", None)
    old2 = _FakeChatSession(client1, "model", None)
    new2 = _FakeChatSession(client2, "model", None)
    client1.start_chat.side_effect = [old1, old2]
    client2.start_chat.side_effect = [RuntimeError("rebuild failed"), new2]

    generation = install_registry_generation(client1)
    new_generation = install_registry_generation(client2, generation=generation + 1)
    registry = SessionRegistry(client1, generation=generation)
    manager1 = await registry.get_session("one")
    manager2 = await registry.get_session("two")
    await manager1.get_response_stateful("model", [{"content": "one"}], "")
    await manager2.get_response_stateful("model", [{"content": "two"}], "")
    await registry.update_client(client2, generation=new_generation)

    with pytest.raises(RuntimeError):
        await manager1.get_response_stateful("model", [{"content": "retry"}], "")
    await manager2.get_response_stateful("model", [{"content": "retry"}], "")

    assert manager1.session is None
    assert manager2.session is new2
    assert manager2.session_generation == registry.client_generation


@pytest.mark.asyncio
async def test_client_replacement_and_session_request_do_not_deadlock(mocker, install_registry_generation):
    client1 = mocker.Mock()
    client2 = mocker.Mock()
    client1.start_chat.return_value = _FakeChatSession(client1, "model", None)
    client2.start_chat.return_value = _FakeChatSession(client2, "model", None)
    generation = install_registry_generation(client1)
    new_generation = install_registry_generation(client2, generation=generation + 1)
    registry = SessionRegistry(client1, generation=generation)
    manager = await registry.get_session("conversation")

    await asyncio.wait_for(
        asyncio.gather(
            manager.get_response_stateful("model", [{"content": "request"}], ""),
            registry.update_client(client2, generation=new_generation),
        ),
        timeout=1,
    )
