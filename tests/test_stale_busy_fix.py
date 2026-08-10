import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from app.services.browser.tab import TabStatus

@pytest.mark.asyncio
async def test_requested_conversation_id_released_after_promotion_mismatch(mocker):
    """
    Verifies that if a requested conversation_id (A) is reserved, 
    but the request is promoted to a different conversation_id (B),
    both are released when the lease is closed.
    """
    # Patch CONFIG before importing/using ProviderSession
    from app.config import CONFIG
    mocker.patch.object(CONFIG, 'getint', return_value=10)
    mocker.patch.object(CONFIG, 'getboolean', return_value=True)
    
    from app.services.browser.session import ProviderSession

    mock_engine = MagicMock()
    mock_engine.is_shutting_down = False
    mock_engine.browser_generation = 1
    mock_engine.max_pages = 10
    
    session = ProviderSession(mock_engine, "test_provider")
    session.ensure_healthy = AsyncMock()
    session.last_browser_generation = 1
    session.context = AsyncMock()
    mock_page = MagicMock()
    mock_page.is_closed.return_value = False
    mock_page.close = AsyncMock()
    session.context.new_page.return_value = mock_page
    session._setup_page_bridge = AsyncMock()
    session.engine.enforce_soft_cap = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=1)

    requested_cid = "requested_A"
    actual_cid = "actual_B"
    req_id = "request_1"

    # 1. Acquire lease with requested_cid
    lease = await session.acquire_lease(conversation_id=requested_cid, request_id=req_id)
    
    assert requested_cid in session.active_conversations
    assert session.active_conversations[requested_cid] == req_id
    assert lease.reserved_conversation_id == requested_cid

    # 2. Promote to actual_cid (different from requested_cid)
    tab = await session.register_conversation(actual_cid, lease)
    
    assert actual_cid in session.active_conversations
    assert session.active_conversations[actual_cid] == req_id
    assert lease.persistent_tab is tab
    assert lease.persistent_tab.conversation_id == actual_cid

    # 3. Close the lease
    await lease.close()

    # 4. BOTH IDs must be released
    assert requested_cid not in session.active_conversations
    assert actual_cid not in session.active_conversations
    assert session.active_lease_count == 0

@pytest.mark.asyncio
async def test_requested_conversation_id_released_on_new_tab_no_promotion(mocker):
    """
    Verifies that if a requested conversation_id (A) is reserved,
    and a new tab is opened but never promoted,
    the requested ID is still released when the lease is closed.
    """
    # Patch CONFIG before importing/using ProviderSession
    from app.config import CONFIG
    mocker.patch.object(CONFIG, 'getint', return_value=10)
    mocker.patch.object(CONFIG, 'getboolean', return_value=True)
    
    from app.services.browser.session import ProviderSession

    mock_engine = MagicMock()
    mock_engine.is_shutting_down = False
    mock_engine.browser_generation = 1
    mock_engine.max_pages = 10
    
    session = ProviderSession(mock_engine, "test_provider")
    session.ensure_healthy = AsyncMock()
    session.last_browser_generation = 1
    session.context = AsyncMock()
    mock_page = MagicMock()
    mock_page.is_closed.return_value = False
    mock_page.close = AsyncMock()
    session.context.new_page.return_value = mock_page
    session._setup_page_bridge = AsyncMock()
    session.engine.enforce_soft_cap = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=1)

    requested_cid = "requested_A"
    req_id = "request_1"

    # 1. Acquire lease
    lease = await session.acquire_lease(conversation_id=requested_cid, request_id=req_id)
    assert requested_cid in session.active_conversations

    # 2. Close lease without promoting
    await lease.close()

    # 3. ID must be released
    assert requested_cid not in session.active_conversations

@pytest.mark.asyncio
async def test_stale_finalizer_protection_for_reserved_id(mocker):
    """
    Verifies that if a reserved_conversation_id (A) has been taken over by 
    another request_id before the current lease closes, the closure
    MUST NOT clear the reservation.
    """
    from app.config import CONFIG
    mocker.patch.object(CONFIG, 'getint', return_value=10)
    mocker.patch.object(CONFIG, 'getboolean', return_value=True)
    
    from app.services.browser.session import ProviderSession

    mock_engine = MagicMock()
    mock_engine.is_shutting_down = False
    mock_engine.browser_generation = 1
    mock_engine.max_pages = 10
    
    session = ProviderSession(mock_engine, "test_provider")
    session.ensure_healthy = AsyncMock()
    session.last_browser_generation = 1
    session.context = AsyncMock()
    mock_page = MagicMock()
    mock_page.is_closed.return_value = False
    mock_page.close = AsyncMock()
    session.context.new_page.return_value = mock_page
    session._setup_page_bridge = AsyncMock()
    session.engine.enforce_soft_cap = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=1)

    requested_cid = "requested_A"
    req_id_stale = "request_stale"
    req_id_new = "request_new"

    # 1. Acquire lease (Simulates old request)
    lease = await session.acquire_lease(conversation_id=requested_cid, request_id=req_id_stale)
    assert session.active_conversations[requested_cid] == req_id_stale

    # 2. Manually simulate "A" being taken over by a newer request
    # In real scenarios, this happens if the original request was cancelled/abandoned
    # and a new one managed to reserve the ID after a timeout or manual clear.
    async with session.conversation_lock:
        session.active_conversations[requested_cid] = req_id_new

    # 3. Close the stale lease
    await lease.close()

    # 4. Protection: requested_cid MUST STILL BE OWNED by req_id_new
    assert requested_cid in session.active_conversations
    assert session.active_conversations[requested_cid] == req_id_new
