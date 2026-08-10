import asyncio
import pytest
from unittest.mock import Mock, AsyncMock
from app.services.browser.session import ProviderSession
from app.services.browser.errors import ConversationBusyError
from app.services.browser.tab import PersistentTab, ManagedPage

@pytest.mark.asyncio
async def test_concurrent_same_conversation_reservation_rejection(tmp_path):
    """Verifies that concurrent requests targeting the same conversation_id
    cannot both pass the reservation phase; the second must fail-fast immediately.
    """
    mock_engine = Mock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    
    session = ProviderSession(mock_engine, "test_provider")
    
    # Mock ensure_healthy and registry populating to allow lease creation to succeed
    session.ensure_healthy = AsyncMock()
    mock_page = Mock()
    mock_page.is_closed.return_value = False
    session.context = Mock()
    session.context.new_page = AsyncMock(return_value=mock_page)
    session.engine.enforce_soft_cap = AsyncMock()
    
    cid = "shared_conversation"
    req_id_1 = "req_1"
    req_id_2 = "req_2"
    
    # Trigger first reservation
    await session.acquire_lease(conversation_id=cid, request_id=req_id_1)
    assert session.active_conversations[cid] == req_id_1
    
    # Trigger second request targeting the same conversation_id
    with pytest.raises(ConversationBusyError) as excinfo:
        await session.acquire_lease(conversation_id=cid, request_id=req_id_2)
    
    assert "busy" in str(excinfo.value)
    # The active conversation ownership MUST remain with the original owner
    assert session.active_conversations[cid] == req_id_1


@pytest.mark.asyncio
async def test_failed_acquisition_guarded_rollback(tmp_path):
    """Verifies that if lease acquisition fails after reservation,
    the reservation is successfully rolled back and leaves no ownership residue.
    """
    mock_engine = Mock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    
    session = ProviderSession(mock_engine, "test_provider")
    
    # Mock ensure_healthy to deliberately fail
    session.ensure_healthy = AsyncMock(side_effect=RuntimeError("Deliberate setup failure"))
    
    cid = "test_conversation"
    req_id = "req_fail"
    
    # Acquisition must fail, but ownership should be cleanly rolled back
    with pytest.raises(RuntimeError) as excinfo:
        await session.acquire_lease(conversation_id=cid, request_id=req_id)
        
    assert "Deliberate setup failure" in str(excinfo.value)
    # Check that reservation is removed
    assert cid not in session.active_conversations


@pytest.mark.asyncio
async def test_stale_cleanup_ownership_overwrite_protection(tmp_path):
    """Verifies that a stale rollback or cleanup path (from an old request)
    is blocked from clearing or mutating a newer active ownership registered under a different request_id.
    """
    mock_engine = Mock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    
    session = ProviderSession(mock_engine, "test_provider")
    
    cid = "test_conversation"
    req_old = "req_old"
    req_new = "req_new"
    
    # 1. Simulate new request having already acquired/overwritten ownership
    async with session.conversation_lock:
        session.active_conversations[cid] = req_new
        
    # 2. Old request attempts to close its lease. Its cleanup path MUST NOT clear Request B's ownership.
    mock_page = Mock()
    mock_page.is_closed = Mock(return_value=False)
    old_lease = ManagedPage(mock_page, session, request_id=req_old)
    
    # Mock tab is present to trigger the conditional check
    mock_tab = Mock()
    mock_tab.conversation_id = cid
    old_lease.persistent_tab = mock_tab
    
    # Execute shielded cleanup for the old lease
    await old_lease.close()
    
    # Verify that Request Old did not clear Request New's ownership
    assert session.active_conversations[cid] == req_new


@pytest.mark.asyncio
async def test_lock_separation_and_deadlock_prevention(tmp_path):
    """Verifies that ownership rollback/finalization paths do not require registry access,
    do not acquire registry_lock, and cannot deadlock with registry operations.
    """
    mock_engine = Mock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    
    session = ProviderSession(mock_engine, "test_provider")
    
    cid = "test_conversation"
    req_id = "req_lock_test"
    
    # 1. Acquire registry_lock to simulate ongoing registry sweep
    await session.registry_lock.acquire()
    
    try:
        # 2. Simulate old request closing its lease (triggering conditional rollback/release)
        # This MUST complete immediately without blocking on registry_lock
        mock_page = Mock()
        mock_page.is_closed = Mock(return_value=False)
        lease = ManagedPage(mock_page, session, request_id=req_id)
        mock_tab = Mock()
        mock_tab.conversation_id = cid
        lease.persistent_tab = mock_tab
        
        # Act: Execute cleanup. If it nests registry_lock, it will deadlock here.
        # We wrap in wait_for with a timeout to verify it executes immediately.
        await asyncio.wait_for(lease.close(), timeout=0.5)
        
    finally:
        session.registry_lock.release()
