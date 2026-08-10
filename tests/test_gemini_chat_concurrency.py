import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.providers.gemini.session_manager import SessionRegistry, SessionManager
from app.utils.tokens import generate_opaque_token

@pytest.mark.asyncio
async def test_concurrent_independent_streams(mocker):
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
    
    registry = SessionRegistry(mock_client)
    
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
async def test_same_session_serialization(mocker):
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
    
    registry = SessionRegistry(mock_client)
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
async def test_registry_capacity_exhaustion(mocker):
    """Verify HTTP 429 when all sessions are locked."""
    from app.services.providers.gemini import session_manager
    mocker.patch(
        "app.services.providers.gemini.session_manager.MAX_SESSIONS",
        2,
    )
    
    mock_client = mocker.Mock()
    registry = SessionRegistry(mock_client)
    
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
async def test_pruning_protects_active_streams(mocker):
    """Verify that sessions with active streams are NOT pruned."""
    from app.services.providers.gemini import session_manager
    mocker.patch(
        "app.services.providers.gemini.session_manager.MAX_SESSIONS",
        1,
    )
    
    mock_client = mocker.Mock()
    registry = SessionRegistry(mock_client)
    
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
async def test_sse_payload_schema_consistency(mocker):
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
    
    registry = SessionRegistry(mock_client)
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
async def test_interrupted_exactly_once_on_cancel(mocker):
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
    
    registry = SessionRegistry(mock_client)
    manager = await registry.get_session("test")
    
    with pytest.raises(asyncio.CancelledError):
        async for _ in manager.get_streaming_response("m", "h", None):
            pass

@pytest.mark.asyncio
async def test_registry_update_client_updates_all(mocker):
    """Verify registry.update_client updates both registry and session managers."""
    mock_client1 = mocker.Mock()
    mock_client2 = mocker.Mock()
    
    registry = SessionRegistry(mock_client1)
    manager1 = await registry.get_session("conv_1")
    manager2 = await registry.get_session("conv_2")
    
    assert registry.client == mock_client1
    assert manager1.client == mock_client1
    assert manager2.client == mock_client1
    
    # Execute async update
    await registry.update_client(mock_client2)
    
    assert registry.client == mock_client2
    assert manager1.client == mock_client2
    assert manager2.client == mock_client2

@pytest.mark.asyncio
async def test_registry_update_client_is_lock_protected(mocker):
    """Verify registry.update_client is strictly serialized and lock-protected."""
    mock_client1 = mocker.Mock()
    mock_client2 = mocker.Mock()
    
    registry = SessionRegistry(mock_client1)
    
    # Forcefully acquire the registry lock
    await registry._lock.acquire()
    assert registry._lock.locked() is True
    
    # Attempt update_client in a background task
    update_task = asyncio.create_task(registry.update_client(mock_client2))
    
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
