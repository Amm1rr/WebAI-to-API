import asyncio
import time
import pytest
from unittest.mock import Mock, AsyncMock

from app.services.browser.engine import BrowserEngine
from app.services.browser.session import ProviderSession
from app.services.browser.tab import TabStatus, PersistentTab

@pytest.fixture
def mock_engine():
    engine = Mock(spec=BrowserEngine)
    engine.max_total_tabs = 2
    engine.is_shutting_down = False
    engine.browser_generation = 1
    engine.sessions = {}
    return engine

@pytest.fixture
def mock_session(mock_engine):
    session = Mock(spec=ProviderSession)
    session.lease_timeout = 60
    session.get_eviction_candidates = AsyncMock(return_value=[])
    mock_engine.sessions = {"test_provider": session}
    return session

def create_mock_tab(cid: str, status: TabStatus, last_accessed: float, last_heartbeat: float = None) -> PersistentTab:
    """
    Helper to generate mock PersistentTab objects for testing eviction targets.

    NOTE: This mocks only the surface attributes consumed by enforce_soft_cap:
    - conversation_id, status, last_accessed_at, last_heartbeat_at, lease_token
    - _lock (for acquire/release), close() (for async cleanup)

    If enforce_soft_cap is extended to access additional attributes or methods,
    this mock must be updated accordingly to avoid false-positive results.
    """
    tab = Mock(spec=PersistentTab)
    tab.conversation_id = cid
    tab.status = status
    tab.last_accessed_at = last_accessed
    tab.last_heartbeat_at = last_heartbeat if last_heartbeat is not None else last_accessed
    tab._lock = asyncio.Lock()
    tab.close = AsyncMock()
    # Mock invalidating closure behavior successfully
    async def mock_close():
        tab.status = TabStatus.DEAD
    tab.close.side_effect = mock_close

    return tab

@pytest.mark.asyncio
async def test_enforce_soft_cap_evicts_idle_lru_tabs(mock_engine, mock_session, mocker):
    """Old idle tabs are the first eviction candidates."""
    mock_engine.total_page_count = 3 # 3 > 2 (max_total_tabs)

    now = time.monotonic()
    tab_oldest = create_mock_tab("oldest", TabStatus.IDLE, now - 100)
    tab_newer = create_mock_tab("newer", TabStatus.IDLE, now - 10)

    mock_session.get_eviction_candidates.return_value = [tab_newer, tab_oldest]

    # Needs to use the real engine logic for enforce_soft_cap
    real_engine = BrowserEngine(headless=True)
    real_engine.max_total_tabs = 2
    real_engine.sessions = {"test_provider": mock_session}

    # Fixture-safe property monkeypatch (isolated to this test)
    mocker.patch.object(type(real_engine), 'total_page_count', property(lambda self: 3))

    await real_engine.enforce_soft_cap()

    # Only oldest should be evicted because needed_evictions = 3 - 2 = 1
    assert tab_oldest.close.called
    assert not tab_newer.close.called
    assert tab_oldest.status == TabStatus.DEAD

@pytest.mark.asyncio
async def test_enforce_soft_cap_does_not_evict_fresh_leased_tab(mock_engine, mock_session, mocker):
    """Active requests are protected."""
    real_engine = BrowserEngine(headless=True)
    real_engine.max_total_tabs = 1
    real_engine.sessions = {"test_provider": mock_session}

    now = time.monotonic()
    tab_fresh = create_mock_tab("fresh", TabStatus.LEASED, now, last_heartbeat=now)
    tab_fresh.lease_token = "token123"

    mock_session.get_eviction_candidates.return_value = [tab_fresh]

    # Fixture-safe property monkeypatch (need 1 eviction)
    mocker.patch.object(type(real_engine), 'total_page_count', property(lambda self: 2))

    await real_engine.enforce_soft_cap()

    # Should NOT be evicted because heartbeat is fresh
    assert not tab_fresh.close.called
    assert tab_fresh.status == TabStatus.LEASED

@pytest.mark.asyncio
async def test_enforce_soft_cap_can_evict_stale_leased_tab(mock_engine, mock_session, mocker):
    """Abandoned leased tabs remain reclaimable."""
    real_engine = BrowserEngine(headless=True)
    real_engine.max_total_tabs = 1
    real_engine.sessions = {"test_provider": mock_session}
    mock_session.lease_timeout = 60

    now = time.monotonic()
    # Heartbeat is older than lease_timeout
    tab_stale = create_mock_tab("stale", TabStatus.LEASED, now - 100, last_heartbeat=now - 100)
    tab_stale.lease_token = "token123"

    mock_session.get_eviction_candidates.return_value = [tab_stale]

    # Fixture-safe property monkeypatch (need 1 eviction)
    mocker.patch.object(type(real_engine), 'total_page_count', property(lambda self: 2))

    await real_engine.enforce_soft_cap()

    assert tab_stale.close.called
    assert tab_stale.status == TabStatus.DEAD

@pytest.mark.asyncio
async def test_enforce_soft_cap_prefers_idle_over_leased(mock_engine, mock_session, mocker):
    """Idle tabs are preferred eviction targets over leased tabs."""
    real_engine = BrowserEngine(headless=True)
    real_engine.max_total_tabs = 1
    real_engine.sessions = {"test_provider": mock_session}
    mock_session.lease_timeout = 60

    now = time.monotonic()
    # Stale leased tab
    tab_stale_leased = create_mock_tab("stale_leased", TabStatus.LEASED, now - 100, last_heartbeat=now - 100)
    tab_stale_leased.lease_token = "token123"

    # Idle tab (even if newer)
    tab_idle = create_mock_tab("idle", TabStatus.IDLE, now - 50)

    mock_session.get_eviction_candidates.return_value = [tab_stale_leased, tab_idle]

    # Fixture-safe property monkeypatch (need 1 eviction)
    mocker.patch.object(type(real_engine), 'total_page_count', property(lambda self: 2))

    await real_engine.enforce_soft_cap()

    # The IDLE tab must be preferred and evicted first
    assert tab_idle.close.called
    assert not tab_stale_leased.close.called

@pytest.mark.asyncio
async def test_enforce_soft_cap_preserves_ownership_invariants(mock_engine, mock_session, mocker):
    """Soft cap enforcement must not violate lease ownership rules."""
    real_engine = BrowserEngine(headless=True)
    real_engine.max_total_tabs = 1
    real_engine.sessions = {"test_provider": mock_session}
    mock_session.lease_timeout = 60

    now = time.monotonic()
    # Simulate a tab that *just* got a new lease token, despite having an old heartbeat.
    # The lack of a valid stale condition (because maybe the heartbeat didn't update yet,
    # or the token was cleared/re-acquired in a race) should prevent blind teardown.
    tab_active = create_mock_tab("active_race", TabStatus.LEASED, now - 100, last_heartbeat=now)
    tab_active.lease_token = "active_token"

    mock_session.get_eviction_candidates.return_value = [tab_active]

    # Fixture-safe property monkeypatch (need 1 eviction)
    mocker.patch.object(type(real_engine), 'total_page_count', property(lambda self: 2))

    await real_engine.enforce_soft_cap()

    # Must NOT be evicted, ownership and token remain valid
    assert not tab_active.close.called
    assert tab_active.status == TabStatus.LEASED
    assert tab_active.lease_token == "active_token"

@pytest.mark.asyncio
async def test_enforce_soft_cap_aborts_if_cap_satisfied(mock_engine, mock_session, mocker):
    """Does nothing if the total page count is below or equal to max tabs."""
    real_engine = BrowserEngine(headless=True)
    real_engine.max_total_tabs = 5
    real_engine.sessions = {"test_provider": mock_session}

    now = time.monotonic()
    tab_idle = create_mock_tab("idle", TabStatus.IDLE, now - 100)
    mock_session.get_eviction_candidates.return_value = [tab_idle]

    # Fixture-safe property monkeypatch (cap not reached)
    mocker.patch.object(type(real_engine), 'total_page_count', property(lambda self: 3))

    await real_engine.enforce_soft_cap()

    assert not tab_idle.close.called
