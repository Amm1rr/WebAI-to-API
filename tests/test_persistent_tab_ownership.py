import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.browser.tab import ManagedPage, PersistentTab, TabStatus


def make_page():
    page = MagicMock()
    page.is_closed.return_value = False
    page.close = AsyncMock()
    page.evaluate = AsyncMock(return_value=1)
    return page


@pytest.mark.asyncio
async def test_persistent_tab_acquire_release_token_flow():
    page = make_page()
    tab = PersistentTab(page, "conversation-1", generation=1)

    token = await tab.acquire_lease("request-1")

    assert token is not None
    assert tab.status == TabStatus.LEASED
    assert tab.lease_token == token
    assert tab.owner_request_id == "request-1"
    assert tab.leased_at is not None

    released = await tab.release_lease(token)

    assert released is True
    assert tab.status == TabStatus.IDLE
    assert tab.lease_token is None
    assert tab.owner_request_id is None
    assert tab.leased_at is None
    assert page.evaluate.await_count == 1


@pytest.mark.asyncio
async def test_persistent_tab_concurrent_acquire_single_owner():
    page = make_page()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def gated_evaluate(_script):
        first_entered.set()
        await release_first.wait()
        return 1

    page.evaluate = AsyncMock(side_effect=gated_evaluate)
    tab = PersistentTab(page, "conversation-1", generation=1)

    first_task = asyncio.create_task(tab.acquire_lease("request-1"))
    await first_entered.wait()
    second_task = asyncio.create_task(tab.acquire_lease("request-2"))
    release_first.set()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(second_task), timeout=0.05)

    second_task.cancel()
    first_token = await first_task

    assert first_token is not None
    assert tab.status == TabStatus.LEASED
    assert tab.owner_request_id == "request-1"
    assert page.evaluate.await_count == 1

    await tab.release_lease(first_token)
    with pytest.raises(asyncio.CancelledError):
        await second_task

    assert tab.status == TabStatus.IDLE
    assert tab.owner_request_id is None


@pytest.mark.asyncio
async def test_persistent_tab_rejects_wrong_release_token():
    page = make_page()
    tab = PersistentTab(page, "conversation-1", generation=1)

    token = await tab.acquire_lease("request-1")
    released = await tab.release_lease("wrong-token")

    assert released is False
    assert tab.status == TabStatus.LEASED
    assert tab.lease_token == token
    assert tab.owner_request_id == "request-1"
    assert tab.leased_at is not None
    assert page.evaluate.await_count == 1

    await tab.release_lease(token)


@pytest.mark.asyncio
async def test_persistent_tab_release_clears_lease_metadata():
    page = make_page()
    tab = PersistentTab(page, "conversation-1", generation=1)

    token = await tab.acquire_lease("request-1")
    leased_at = tab.leased_at

    assert leased_at is not None
    released = await tab.release_lease(token)

    assert released is True

    assert tab.lease_token is None
    assert tab.owner_request_id is None
    assert tab.leased_at is None
    assert tab.last_accessed_at >= leased_at


@pytest.mark.asyncio
async def test_managed_page_close_releases_once():
    page = make_page()
    session = MagicMock()
    session.name = "gemini"
    session.engine = MagicMock()
    session.engine.browser_generation = 1
    session.conversation_lock = asyncio.Lock()
    session.active_conversations = {
        "conversation-1": "request-1",
        "reserved-1": "request-1",
    }
    session.semaphore = MagicMock()
    session.semaphore.release = MagicMock()
    session.active_lease_count = 1

    persistent_tab = MagicMock()
    persistent_tab.conversation_id = "conversation-1"
    persistent_tab.release_lease = AsyncMock(return_value=True)
    persistent_tab.status = TabStatus.IDLE
    persistent_tab.close = AsyncMock()

    lease = ManagedPage(
        page,
        session,
        persistent_tab=persistent_tab,
        lease_token="lease-token",
        request_id="request-1",
        reserved_conversation_id="reserved-1",
    )

    await lease.close()
    await lease.close()

    persistent_tab.release_lease.assert_awaited_once_with("lease-token")
    session.semaphore.release.assert_called_once()
    assert session.active_lease_count == 0
    assert "conversation-1" not in session.active_conversations
    assert "reserved-1" not in session.active_conversations
