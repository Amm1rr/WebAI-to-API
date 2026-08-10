import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.browser.session import ProviderSession
from app.services.browser.tab import PersistentTab, TabStatus


def make_engine(generation=1):
    browser = MagicMock()
    browser.is_connected.return_value = True

    engine = MagicMock()
    engine.max_pages = 3
    engine.browser = browser
    engine.browser_generation = generation
    engine.is_shutting_down = False
    engine.management_lock = asyncio.Lock()
    engine._ensure_healthy_browser = AsyncMock()
    engine.enforce_soft_cap = AsyncMock()
    return engine


def make_page():
    page = MagicMock()
    page.is_closed.return_value = False
    page.close = AsyncMock()
    page.evaluate = AsyncMock(return_value=1)
    return page


@pytest.mark.asyncio
async def test_ensure_healthy_purges_tabs_on_generation_rollover(mocker):
    engine = make_engine(generation=2)
    session = ProviderSession(engine, "test_provider")
    session.last_browser_generation = 1
    keepalive_page = make_page()

    async def mark_session_healthy():
        session.context = MagicMock()
        session.keepalive_page = keepalive_page
        session.last_browser_generation = engine.browser_generation

    session._setup = AsyncMock(side_effect=mark_session_healthy)
    schedule_orphan_cleanup = mocker.patch.object(session, "_schedule_orphan_cleanup")

    idle_page = make_page()
    leased_page = make_page()
    idle_tab = PersistentTab(idle_page, "idle-conversation", generation=1)
    leased_tab = PersistentTab(leased_page, "leased-conversation", generation=1)
    leased_tab.status = TabStatus.LEASED
    leased_tab.lease_token = "leased-token"
    session.conversation_registry = {
        idle_tab.conversation_id: idle_tab,
        leased_tab.conversation_id: leased_tab,
    }

    await session.ensure_healthy()

    engine._ensure_healthy_browser.assert_awaited_once()
    assert session.conversation_registry == {}
    assert idle_tab.status == TabStatus.DEAD
    idle_page.close.assert_awaited_once()
    assert leased_tab.status == TabStatus.INVALIDATING
    schedule_orphan_cleanup.assert_called_once_with(leased_tab)
    assert session.is_alive is True


@pytest.mark.asyncio
async def test_acquire_lease_discards_stale_generation_tab():
    engine = make_engine(generation=2)
    session = ProviderSession(engine, "test_provider")
    session.ensure_healthy = AsyncMock()
    session._setup_page_bridge = AsyncMock()

    stale_page = make_page()
    new_page = make_page()
    session.context = MagicMock()
    session.context.new_page = AsyncMock(return_value=new_page)

    conversation_id = "stale-conversation"
    stale_tab = PersistentTab(stale_page, conversation_id, generation=1)
    session.conversation_registry[conversation_id] = stale_tab

    lease = await session.acquire_lease(
        conversation_id=conversation_id,
        request_id="request-1",
    )

    assert conversation_id not in session.conversation_registry
    assert stale_tab.status == TabStatus.DEAD
    stale_page.close.assert_awaited_once()
    assert lease.page is new_page
    assert lease.persistent_tab is None
    session.context.new_page.assert_awaited_once()

    await lease.close()


@pytest.mark.asyncio
async def test_generation_rollover_during_tab_acquire_closes_tab():
    engine = make_engine(generation=1)
    session = ProviderSession(engine, "test_provider")
    session.ensure_healthy = AsyncMock()
    session._setup_page_bridge = AsyncMock()

    conversation_id = "rolling-conversation"
    stale_page = make_page()

    async def rollover_during_probe(_script):
        engine.browser_generation = 2
        return 1

    stale_page.evaluate = AsyncMock(side_effect=rollover_during_probe)
    stale_tab = PersistentTab(stale_page, conversation_id, generation=1)
    session.conversation_registry[conversation_id] = stale_tab

    replacement_page = make_page()
    session.context = MagicMock()
    session.context.new_page = AsyncMock(return_value=replacement_page)

    lease = await session.acquire_lease(
        conversation_id=conversation_id,
        request_id="request-1",
    )

    assert conversation_id not in session.conversation_registry
    assert stale_tab.status == TabStatus.DEAD
    assert stale_tab.lease_token is None
    assert stale_tab.owner_request_id is None
    assert stale_tab.leased_at is None
    stale_page.evaluate.assert_awaited_once_with("1")
    stale_page.close.assert_awaited_once()
    assert lease.page is replacement_page
    assert lease.persistent_tab is None
    session.context.new_page.assert_awaited_once()

    await lease.close()
