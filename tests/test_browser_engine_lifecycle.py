import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.browser.engine import BrowserEngine
from app.services.browser.errors import BrowserShuttingDownError


def make_browser():
    browser = MagicMock()
    browser.close = AsyncMock()
    browser.is_connected.return_value = True
    return browser


@pytest.mark.asyncio
async def test_engine_close_sets_terminal_shutdown_and_closes_owned_resources():
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 0
    session.close_resources = AsyncMock()

    engine.browser = browser
    engine.playwright = playwright
    engine.sessions = {"gemini": session}

    await engine.close()

    assert engine.is_shutting_down is True
    assert engine._shutdown_started is True
    session.close_resources.assert_awaited_once_with(save_state=True)
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert engine.sessions == {}
    assert engine.browser is None
    assert engine.playwright is None


@pytest.mark.asyncio
async def test_engine_close_is_idempotent():
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 0
    session.close_resources = AsyncMock()

    engine.browser = browser
    engine.playwright = playwright
    engine.sessions = {"gemini": session}

    await engine.close()
    await engine.close()

    session.close_resources.assert_awaited_once_with(save_state=True)
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_healthy_browser_noops_after_shutdown(mocker):
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    browser.is_connected.return_value = False
    playwright = MagicMock()
    playwright.stop = AsyncMock()

    engine.browser = browser
    engine.playwright = playwright
    engine.browser_generation = 4
    engine.is_shutting_down = True
    async_playwright = mocker.patch("app.services.browser.engine.async_playwright")

    async with engine.management_lock:
        await engine._ensure_healthy_browser()

    async_playwright.assert_not_called()
    assert engine.browser_generation == 4
    assert engine.browser is browser
    assert engine.playwright is playwright


@pytest.mark.asyncio
async def test_get_page_after_shutdown_fails_fast():
    engine = BrowserEngine(headless=True)
    engine.is_shutting_down = True

    with pytest.raises(BrowserShuttingDownError):
        await engine.get_page("gemini")


@pytest.mark.asyncio
async def test_browser_replacement_closes_provider_context_before_old_browser(mocker):
    engine = BrowserEngine(headless=True)
    order = []
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_browser.close.side_effect = lambda: order.append("browser")
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock(side_effect=lambda: order.append("playwright"))
    new_browser = make_browser()
    new_playwright = MagicMock()
    new_playwright.chromium.launch = AsyncMock(return_value=new_browser)
    playwright_factory = mocker.patch("app.services.browser.engine.async_playwright")
    playwright_factory.return_value.start = AsyncMock(return_value=new_playwright)

    session = MagicMock()
    session.name = "gemini"
    session._has_resources_to_close.return_value = True
    session.context = object()

    async def close_resources(*, save_state):
        assert save_state is False
        assert session.context is not None
        session.context = None
        order.append("session")

    session.close_resources = AsyncMock(side_effect=close_resources)
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.browser_generation = 4
    engine.sessions = {"gemini": session}

    async with engine.management_lock:
        await engine._ensure_healthy_browser()

    assert order == ["session", "browser", "playwright"]
    assert session.context is None
    assert engine.browser is new_browser
    assert engine.browser_generation == 5
    session.close_resources.assert_awaited_once_with(save_state=False)


@pytest.mark.asyncio
async def test_browser_replacement_skips_provider_sessions_without_resources(mocker):
    engine = BrowserEngine(headless=True)
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock()
    new_browser = make_browser()
    new_playwright = MagicMock()
    new_playwright.chromium.launch = AsyncMock(return_value=new_browser)
    playwright_factory = mocker.patch("app.services.browser.engine.async_playwright")
    playwright_factory.return_value.start = AsyncMock(return_value=new_playwright)

    session = MagicMock()
    session._has_resources_to_close.return_value = False
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.sessions = {"empty": session}

    async with engine.management_lock:
        await engine._ensure_healthy_browser()

    session.close_resources.assert_not_called()
    assert engine.browser_generation == 1


@pytest.mark.asyncio
async def test_browser_replacement_cleans_multiple_provider_sessions(mocker):
    engine = BrowserEngine(headless=True)
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock()
    new_browser = make_browser()
    new_playwright = MagicMock()
    new_playwright.chromium.launch = AsyncMock(return_value=new_browser)
    playwright_factory = mocker.patch("app.services.browser.engine.async_playwright")
    playwright_factory.return_value.start = AsyncMock(return_value=new_playwright)

    sessions = []
    for name in ("gemini", "other"):
        session = MagicMock()
        session.name = name
        session._has_resources_to_close.return_value = True
        session.close_resources = AsyncMock()
        sessions.append(session)
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.sessions = {session.name: session for session in sessions}

    async with engine.management_lock:
        await engine._ensure_healthy_browser()

    for session in sessions:
        session.close_resources.assert_awaited_once_with(save_state=False)


@pytest.mark.asyncio
async def test_browser_replacement_cleanup_failure_stops_before_parent_close(mocker):
    engine = BrowserEngine(headless=True)
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock()
    session = MagicMock()
    session._has_resources_to_close.return_value = True
    session.close_resources = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.sessions = {"gemini": session}

    with pytest.raises(RuntimeError, match="cleanup failed"):
        async with engine.management_lock:
            await engine._ensure_healthy_browser()

    old_browser.close.assert_not_awaited()
    assert engine.browser_generation == 0


@pytest.mark.asyncio
async def test_browser_replacement_browser_close_failure_preserves_launch_policy(mocker):
    engine = BrowserEngine(headless=True)
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_browser.close.side_effect = RuntimeError("old browser close failed")
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock()
    new_browser = make_browser()
    new_playwright = MagicMock()
    new_playwright.chromium.launch = AsyncMock(return_value=new_browser)
    playwright_factory = mocker.patch("app.services.browser.engine.async_playwright")
    playwright_factory.return_value.start = AsyncMock(return_value=new_playwright)
    session = MagicMock()
    session._has_resources_to_close.return_value = False
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.sessions = {"gemini": session}

    async with engine.management_lock:
        await engine._ensure_healthy_browser()

    assert engine.browser is new_browser
    assert engine.browser_generation == 1


@pytest.mark.asyncio
async def test_replacement_setup_creates_context_on_new_generation(mocker):
    engine = BrowserEngine(headless=True, is_bootstrap=True)
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock()
    new_context = MagicMock()
    new_context.on = MagicMock()
    new_context.new_page = AsyncMock(return_value=MagicMock())
    new_browser = make_browser()
    new_browser.new_context = AsyncMock(return_value=new_context)
    new_playwright = MagicMock()
    new_playwright.chromium.launch = AsyncMock(return_value=new_browser)
    playwright_factory = mocker.patch("app.services.browser.engine.async_playwright")
    playwright_factory.return_value.start = AsyncMock(return_value=new_playwright)
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    from app.services.browser.session import ProviderSession

    session = ProviderSession(engine, "gemini", enable_persistence=True)
    session.last_browser_generation = 1
    session._eviction_loop = AsyncMock()
    session._reaper_loop = AsyncMock()
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.browser_generation = 1
    engine.sessions = {"gemini": session}

    await session.ensure_healthy()
    await session.close_resources(save_state=False)

    assert new_browser.new_context.await_count == 1
    assert session.last_browser_generation == 2
    assert session.context is None


@pytest.mark.asyncio
async def test_browser_replacement_launch_failure_does_not_increment_generation(mocker):
    engine = BrowserEngine(headless=True)
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock()
    new_playwright = MagicMock()
    new_playwright.chromium.launch = AsyncMock(side_effect=RuntimeError("launch failed"))
    playwright_factory = mocker.patch("app.services.browser.engine.async_playwright")
    playwright_factory.return_value.start = AsyncMock(return_value=new_playwright)
    session = MagicMock()
    session._has_resources_to_close.return_value = True
    session.close_resources = AsyncMock()
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.browser_generation = 7
    engine.sessions = {"gemini": session}

    with pytest.raises(RuntimeError, match="launch failed"):
        async with engine.management_lock:
            await engine._ensure_healthy_browser()

    assert engine.browser is None
    assert engine.browser_generation == 7


@pytest.mark.asyncio
async def test_provider_health_replacement_does_not_deadlock_cleanup(mocker):
    engine = BrowserEngine(headless=True, is_bootstrap=True)
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock()
    new_context = MagicMock()
    new_context.on = MagicMock()
    new_context.new_page = AsyncMock(return_value=MagicMock())
    new_browser = make_browser()
    new_browser.new_context = AsyncMock(return_value=new_context)
    new_playwright = MagicMock()
    new_playwright.chromium.launch = AsyncMock(return_value=new_browser)
    playwright_factory = mocker.patch("app.services.browser.engine.async_playwright")
    playwright_factory.return_value.start = AsyncMock(return_value=new_playwright)
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    from app.services.browser.session import ProviderSession

    session = ProviderSession(engine, "gemini", enable_persistence=True)
    session.last_browser_generation = 1
    session._eviction_loop = AsyncMock()
    session._reaper_loop = AsyncMock()
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.browser_generation = 1
    engine.sessions = {"gemini": session}

    await asyncio.wait_for(session.ensure_healthy(), timeout=1)

    assert engine.browser_generation == 2
    assert new_browser.new_context.await_count == 1
    assert session.last_browser_generation == 2
    await session.close_resources(save_state=False)


@pytest.mark.asyncio
async def test_recovery_cleanup_completes_before_browser_replacement(mocker):
    engine = BrowserEngine(headless=True, is_bootstrap=True)
    order = []
    old_browser = make_browser()
    old_browser.is_connected.return_value = False
    old_browser.close.side_effect = lambda: order.append("browser")
    old_playwright = MagicMock()
    old_playwright.stop = AsyncMock()
    new_context = MagicMock()
    new_context.on = MagicMock()
    new_context.new_page = AsyncMock(return_value=MagicMock())
    new_browser = make_browser()
    new_browser.new_context = AsyncMock(side_effect=lambda **_: (order.append("context"), new_context)[1])
    new_playwright = MagicMock()
    new_playwright.chromium.launch = AsyncMock(return_value=new_browser)
    playwright_factory = mocker.patch("app.services.browser.engine.async_playwright")
    playwright_factory.return_value.start = AsyncMock(return_value=new_playwright)
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    from app.services.browser.session import ProviderSession

    close_started = asyncio.Event()
    release_close = asyncio.Event()
    old_context = MagicMock()
    old_context.is_closed.return_value = False

    async def close_old_context():
        close_started.set()
        await release_close.wait()
        order.append("recovery")

    old_context.close = AsyncMock(side_effect=close_old_context)
    session = ProviderSession(engine, "gemini", enable_persistence=True)
    session.context = old_context
    session.last_browser_generation = 1
    session._eviction_loop = AsyncMock()
    session._reaper_loop = AsyncMock()
    engine.browser = old_browser
    engine.playwright = old_playwright
    engine.browser_generation = 1
    engine.sessions = {"gemini": session}

    recovery = asyncio.create_task(session._do_session_recovery())
    await close_started.wait()
    replacement = asyncio.create_task(session.ensure_healthy())

    release_close.set()
    await asyncio.gather(recovery, replacement)
    await session.close_resources(save_state=False)

    assert order.index("recovery") < order.index("browser") < order.index("context")
    assert session.last_browser_generation == 2


@pytest.mark.asyncio
async def test_shutdown_waits_for_ensure_healthy_before_closing_browser(mocker):
    engine = BrowserEngine(headless=True, is_bootstrap=True)
    browser = make_browser()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    context = MagicMock()
    context.is_closed.return_value = False
    context.close = AsyncMock()
    context.on = MagicMock()
    context.new_page = AsyncMock(return_value=MagicMock())
    browser.new_context = AsyncMock(return_value=context)
    health_started = asyncio.Event()
    release_health = asyncio.Event()

    async def block_health():
        health_started.set()
        await release_health.wait()

    engine._ensure_healthy_browser = block_health
    engine.browser = browser
    engine.playwright = playwright
    engine.browser_generation = 1
    from app.services.browser.session import ProviderSession

    session = ProviderSession(engine, "gemini", enable_persistence=True)
    session._eviction_loop = AsyncMock()
    session._reaper_loop = AsyncMock()
    engine.sessions = {"gemini": session}
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    ensure_task = asyncio.create_task(session.ensure_healthy())
    await health_started.wait()
    shutdown_task = asyncio.create_task(engine.close())
    assert engine._shutdown_started is False
    release_health.set()
    await ensure_task
    await shutdown_task

    assert engine._shutdown_started is True
    assert browser.close.await_count == 1
    assert context.close.await_count == 1


@pytest.mark.asyncio
async def test_shutdown_first_rejects_ensure_without_context_creation():
    engine = BrowserEngine(headless=True)
    await engine.close()
    from app.services.browser.session import ProviderSession

    session = ProviderSession(engine, "gemini")
    session._setup_locked = AsyncMock()

    with pytest.raises(BrowserShuttingDownError):
        await session.ensure_healthy()

    session._setup_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_waits_for_pending_recovery_cleanup(mocker):
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    context = MagicMock()
    context.is_closed.return_value = False
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_context():
        close_started.set()
        await release_close.wait()

    context.close = AsyncMock(side_effect=close_context)
    engine.browser = browser
    engine.playwright = playwright
    engine.browser_generation = 1
    from app.services.browser.session import ProviderSession

    session = ProviderSession(engine, "gemini")
    session.context = context
    engine.sessions = {"gemini": session}

    recovery = asyncio.create_task(session._do_session_recovery())
    await close_started.wait()
    shutdown = asyncio.create_task(engine.close())
    release_close.set()
    await asyncio.gather(recovery, shutdown)

    assert session.context is None
    assert engine.is_shutting_down is True
    assert browser.close.await_count == 1
