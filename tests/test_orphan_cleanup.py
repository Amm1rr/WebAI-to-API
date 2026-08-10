import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.browser.session import ProviderSession
from app.services.browser.tab import PersistentTab, TabStatus


def make_session():
    engine = MagicMock()
    engine.browser_generation = 1
    engine.is_shutting_down = False
    engine.max_pages = 2
    engine.browser = MagicMock()
    engine.browser.is_connected.return_value = True

    session = ProviderSession(engine, "test_provider")
    session.lease_timeout = 0.1
    return session


def make_tab(conversation_id: str, lease_token: str = "lease-token"):
    page = MagicMock()
    page.is_closed.return_value = False
    page.close = AsyncMock()
    tab = PersistentTab(page, conversation_id, generation=1)
    tab.status = TabStatus.INVALIDATING
    tab.lease_token = lease_token
    tab.owner_request_id = "request-1"
    tab.leased_at = 1.0
    tab.last_heartbeat_at = 1.0
    return tab


@pytest.mark.asyncio
async def test_orphan_cleanup_deduplicates_task(mocker):
    session = make_session()
    tab = make_tab("conversation-1")
    cleanup_task = MagicMock()
    cleanup_task.done.return_value = False
    cleanup_task.cancel = MagicMock()
    create_task_calls = 0

    def fake_create_task(coro):
        nonlocal create_task_calls
        create_task_calls += 1
        coro.close()
        return cleanup_task

    mocker.patch("app.services.browser.session.asyncio.create_task", new=fake_create_task)

    session._schedule_orphan_cleanup(tab)
    session._schedule_orphan_cleanup(tab)

    assert create_task_calls == 1
    assert tab._cleanup_task is cleanup_task
    assert tab in session.active_orphans
    assert cleanup_task in session._orphan_cleanup_tasks


@pytest.mark.asyncio
async def test_orphan_cleanup_closes_stale_unresponsive_tab(mocker):
    session = make_session()
    tab = make_tab("conversation-1")
    tab.last_heartbeat_at = 0.0
    tab.status = TabStatus.INVALIDATING

    async def immediate_sleep(_delay):
        return None

    mocker.patch("app.services.browser.session.asyncio.sleep", new=immediate_sleep)
    mocker.patch("app.services.browser.session.time.monotonic", return_value=1000.0)

    session._schedule_orphan_cleanup(tab)

    task = tab._cleanup_task
    await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

    tab.page.close.assert_awaited_once()
    assert tab.status == TabStatus.DEAD
    assert tab._cleanup_task is None
    assert tab not in session.active_orphans
    assert len(session._orphan_cleanup_tasks) == 0


@pytest.mark.asyncio
async def test_orphan_cleanup_skips_fresh_heartbeat(mocker):
    session = make_session()
    tab = make_tab("conversation-1")
    tab.last_heartbeat_at = 1000.0

    sleep_calls = 0

    async def heartbeat_safe_sleep(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            tab.lease_token = "changed-token"
        return None

    mocker.patch("app.services.browser.session.asyncio.sleep", new=heartbeat_safe_sleep)
    mocker.patch("app.services.browser.session.time.monotonic", return_value=1000.0)

    session._schedule_orphan_cleanup(tab)

    task = tab._cleanup_task
    await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

    tab.page.close.assert_not_awaited()
    assert tab.status == TabStatus.INVALIDATING
    assert tab._cleanup_task is None
    assert tab not in session.active_orphans
    assert len(session._orphan_cleanup_tasks) == 0


@pytest.mark.asyncio
async def test_orphan_cleanup_aborts_on_token_change(mocker):
    session = make_session()
    tab = make_tab("conversation-1")
    tab.last_heartbeat_at = 0.0

    async def token_change_sleep(_delay):
        tab.lease_token = "new-token"
        return None

    mocker.patch("app.services.browser.session.asyncio.sleep", new=token_change_sleep)
    mocker.patch("app.services.browser.session.time.monotonic", return_value=1000.0)

    session._schedule_orphan_cleanup(tab)

    task = tab._cleanup_task
    await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

    tab.page.close.assert_not_awaited()
    assert tab.status == TabStatus.INVALIDATING
    assert tab._cleanup_task is None
    assert tab not in session.active_orphans
    assert len(session._orphan_cleanup_tasks) == 0
