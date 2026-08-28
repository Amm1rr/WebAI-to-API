import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.services.browser.engine import BrowserEngine
from app.services.browser.errors import BrowserShuttingDownError


def make_browser():
    browser = MagicMock()
    browser.close = AsyncMock()
    browser.is_connected.return_value = True
    return browser


def make_runtime():
    runtime = MagicMock()
    runtime.is_browser_connected.return_value = True
    runtime.close_browser = AsyncMock()
    runtime.stop = AsyncMock()
    runtime.start = AsyncMock()
    runtime.launch_browser = AsyncMock(return_value=make_browser())
    runtime.bind_disconnect = MagicMock()
    return runtime


@pytest.mark.asyncio
async def test_engine_close_sets_terminal_shutdown_and_closes_owned_resources():
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    runtime = make_runtime()
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 0
    session.close_resources = AsyncMock()

    engine.runtime = runtime
    engine.browser = browser
    engine.sessions = {"gemini": session}

    await engine.close()

    assert engine.is_shutting_down is True
    assert engine._shutdown_started is True
    session.close_resources.assert_awaited_once_with(save_state=False)
    runtime.close_browser.assert_awaited_once_with(browser, "terminal")
    runtime.stop.assert_awaited_once()
    assert engine.sessions == {}
    assert engine.browser is None


@pytest.mark.asyncio
async def test_application_shutdown_intent_is_idempotent_before_close():
    engine = BrowserEngine(headless=True)

    assert engine.request_shutdown("application") is True
    assert engine.request_shutdown("application") is False
    assert engine.shutdown_requested is True
    assert engine.shutdown_source == "application"
    assert engine._shutdown_started is False

    engine.close = AsyncMock()
    engine._on_browser_disconnected()

    engine.close.assert_not_awaited()
    assert engine._shutdown_started is False


@pytest.mark.asyncio
async def test_application_shutdown_close_preserves_application_source():
    engine = BrowserEngine(headless=True)
    engine.request_shutdown("application")

    await engine.close(source="application")

    assert engine.shutdown_source == "application"
    assert engine._shutdown_started is True


@pytest.mark.asyncio
async def test_bootstrap_engine_close_preserves_persistence_save():
    engine = BrowserEngine(headless=True, is_bootstrap=True)
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 0
    session.close_resources = AsyncMock()
    engine.sessions = {"gemini": session}

    await engine.close()

    session.close_resources.assert_awaited_once_with(save_state=True)


@pytest.mark.asyncio
async def test_bootstrap_engine_close_can_suppress_persistence_save():
    engine = BrowserEngine(headless=True, is_bootstrap=True)
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 0
    session.close_resources = AsyncMock()
    engine.sessions = {"gemini": session}

    await engine.close(save_state=False)

    session.close_resources.assert_awaited_once_with(save_state=False)


@pytest.mark.asyncio
async def test_terminal_close_delegates_to_runtime_and_stops_it():
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    runtime = make_runtime()
    engine.runtime = runtime
    engine.browser = browser

    await engine.close()

    runtime.close_browser.assert_awaited_once_with(browser, "terminal")
    runtime.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_disconnect_source_wins_before_application_intent(mocker):
    engine = BrowserEngine(headless=True)
    scheduled = []
    loop = MagicMock()
    loop.create_task.side_effect = lambda coroutine: scheduled.append(coroutine)
    mocker.patch("app.services.browser.engine.asyncio.get_running_loop", return_value=loop)
    engine.close = AsyncMock()

    engine._on_browser_disconnected()
    engine.request_shutdown("application")

    assert engine.shutdown_source == "browser-disconnect"
    assert len(scheduled) == 1
    scheduled.pop().close()
    engine.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_requested_rejects_new_page_admission():
    engine = BrowserEngine(headless=True)
    engine.request_shutdown("application")

    with pytest.raises(BrowserShuttingDownError):
        await engine.get_page("gemini")


@pytest.mark.asyncio
async def test_shutdown_aborts_active_requests_after_drain_deadline(mocker):
    engine = BrowserEngine(headless=True)
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 1
    session.close_resources = AsyncMock()

    def abort_requests():
        session.active_lease_count = 0

    session.abort_active_requests.side_effect = abort_requests
    engine.sessions = {"gemini": session}
    clock_values = iter((0.0, 16.0))
    real_monotonic = time.monotonic
    mocker.patch(
        "app.services.browser.engine.time.monotonic",
        side_effect=lambda: next(clock_values, real_monotonic()),
    )

    await engine.close()

    session.abort_active_requests.assert_called_once_with()
    session.close_resources.assert_awaited_once_with(save_state=False)


@pytest.mark.asyncio
async def test_shutdown_does_not_abort_request_that_releases_during_grace(mocker):
    engine = BrowserEngine(headless=True)
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 1
    session.close_resources = AsyncMock()

    async def release_during_grace(_delay):
        session.active_lease_count = 0

    session.abort_active_requests = Mock()
    mocker.patch("app.services.browser.engine.asyncio.sleep", side_effect=release_during_grace)
    engine.sessions = {"gemini": session}

    await engine.close()

    session.abort_active_requests.assert_not_called()


@pytest.mark.asyncio
async def test_browser_disconnect_signals_all_active_requests(mocker):
    engine = BrowserEngine(headless=True)
    session = MagicMock()
    engine.sessions = {"gemini": session}
    engine.close = AsyncMock()
    scheduled = []
    loop = MagicMock()
    loop.create_task.side_effect = lambda coroutine: scheduled.append(coroutine)
    mocker.patch("app.services.browser.engine.asyncio.get_running_loop", return_value=loop)

    engine._on_browser_disconnected()

    session.signal_active_requests.assert_called_once()
    assert engine.shutdown_source == "browser-disconnect"
    assert len(scheduled) == 1
    scheduled.pop().close()


@pytest.mark.asyncio
async def test_browser_disconnect_during_shutdown_signals_without_rescheduling(mocker):
    engine = BrowserEngine(headless=True)
    engine.is_shutting_down = True
    session = MagicMock()
    engine.sessions = {"gemini": session}
    engine.close = AsyncMock()
    loop = MagicMock()
    loop.create_task = Mock()
    mocker.patch("app.services.browser.engine.asyncio.get_running_loop", return_value=loop)

    engine._on_browser_disconnected()

    session.signal_active_requests.assert_called_once()
    loop.create_task.assert_not_called()
    engine.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnect_close_task_is_tracked_and_exception_retrieved(caplog):
    engine = BrowserEngine(headless=True)

    async def fail_close(*_args, **_kwargs):
        raise RuntimeError("disconnect close failed")

    engine.close = fail_close
    engine._on_browser_disconnected()
    task = engine._disconnect_close_task

    assert task is not None
    with pytest.raises(RuntimeError, match="disconnect close failed"):
        await task
    await asyncio.sleep(0)

    assert engine._disconnect_close_task is None
    assert any("Disconnect shutdown task failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_engine_close_is_idempotent():
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    runtime = make_runtime()
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 0
    session.close_resources = AsyncMock()

    engine.runtime = runtime
    engine.browser = browser
    engine.sessions = {"gemini": session}

    await engine.close()
    await engine.close()

    session.close_resources.assert_awaited_once_with(save_state=False)
    runtime.close_browser.assert_awaited_once()
    runtime.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_healthy_browser_noops_after_shutdown():
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    runtime = make_runtime()
    engine.runtime = runtime
    engine.browser = browser
    engine.browser_generation = 4
    engine.is_shutting_down = True

    async with engine.management_lock:
        await engine._ensure_healthy_browser()

    runtime.start.assert_not_awaited()
    runtime.launch_browser.assert_not_awaited()
    assert engine.browser_generation == 4
    assert engine.browser is browser


@pytest.mark.asyncio
async def test_get_page_after_shutdown_fails_fast():
    engine = BrowserEngine(headless=True)
    engine.is_shutting_down = True

    with pytest.raises(BrowserShuttingDownError):
        await engine.get_page("gemini")


@pytest.mark.asyncio
async def test_browser_replacement_closes_provider_context_before_old_browser():
    engine = BrowserEngine(headless=True)
    order = []
    runtime = make_runtime()
    runtime.is_browser_connected.return_value = False
    runtime.close_browser = AsyncMock(side_effect=lambda browser, phase: order.append("browser"))
    runtime.stop = AsyncMock(side_effect=lambda: order.append("playwright"))
    new_browser = make_browser()
    runtime.launch_browser = AsyncMock(return_value=new_browser)
    old_browser = make_browser()

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
    engine.runtime = runtime
    engine.browser = old_browser
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
async def test_browser_replacement_skips_provider_sessions_without_resources():
    engine = BrowserEngine(headless=True)
    runtime = make_runtime()
    runtime.is_browser_connected.return_value = False
    new_browser = make_browser()
    runtime.launch_browser = AsyncMock(return_value=new_browser)
    old_browser = make_browser()

    session = MagicMock()
    session._has_resources_to_close.return_value = False
    engine.runtime = runtime
    engine.browser = old_browser
    engine.sessions = {"empty": session}

    async with engine.management_lock:
        await engine._ensure_healthy_browser()

    session.close_resources.assert_not_called()
    assert engine.browser_generation == 1


@pytest.mark.asyncio
async def test_browser_replacement_cleans_multiple_provider_sessions():
    engine = BrowserEngine(headless=True)
    runtime = make_runtime()
    runtime.is_browser_connected.return_value = False
    new_browser = make_browser()
    runtime.launch_browser = AsyncMock(return_value=new_browser)
    old_browser = make_browser()

    sessions = []
    for name in ("gemini", "other"):
        session = MagicMock()
        session.name = name
        session._has_resources_to_close.return_value = True
        session.close_resources = AsyncMock()
        sessions.append(session)
    engine.runtime = runtime
    engine.browser = old_browser
    engine.sessions = {session.name: session for session in sessions}

    async with engine.management_lock:
        await engine._ensure_healthy_browser()

    for session in sessions:
        session.close_resources.assert_awaited_once_with(save_state=False)


@pytest.mark.asyncio
async def test_browser_replacement_cleanup_failure_stops_before_parent_close():
    engine = BrowserEngine(headless=True)
    runtime = make_runtime()
    runtime.is_browser_connected.return_value = False
    old_browser = make_browser()
    session = MagicMock()
    session._has_resources_to_close.return_value = True
    session.close_resources = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    engine.runtime = runtime
    engine.browser = old_browser
    engine.sessions = {"gemini": session}

    with pytest.raises(RuntimeError, match="cleanup failed"):
        async with engine.management_lock:
            await engine._ensure_healthy_browser()

    runtime.close_browser.assert_not_awaited()
    assert engine.browser_generation == 0


@pytest.mark.asyncio
async def test_replacement_setup_creates_context_on_new_generation(mocker):
    engine = BrowserEngine(headless=True, is_bootstrap=True)
    runtime = make_runtime()
    runtime.is_browser_connected.return_value = False
    new_context = MagicMock()
    new_context.on = MagicMock()
    new_context.new_page = AsyncMock(return_value=MagicMock())
    new_browser = make_browser()
    new_browser.new_context = AsyncMock(return_value=new_context)
    runtime.launch_browser = AsyncMock(return_value=new_browser)
    old_browser = make_browser()
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    from app.services.browser.session import ProviderSession

    session = ProviderSession(engine, "gemini", enable_persistence=True)
    session.last_browser_generation = 1
    session._eviction_loop = AsyncMock()
    session._reaper_loop = AsyncMock()
    engine.runtime = runtime
    engine.browser = old_browser
    engine.browser_generation = 1
    engine.sessions = {"gemini": session}

    await session.ensure_healthy()
    await session.close_resources(save_state=False)

    assert new_browser.new_context.await_count == 1
    assert session.last_browser_generation == 2
    assert session.context is None


@pytest.mark.asyncio
async def test_browser_replacement_launch_failure_does_not_increment_generation():
    engine = BrowserEngine(headless=True)
    runtime = make_runtime()
    runtime.is_browser_connected.return_value = False
    runtime.launch_browser = AsyncMock(side_effect=RuntimeError("launch failed"))
    old_browser = make_browser()
    session = MagicMock()
    session._has_resources_to_close.return_value = True
    session.close_resources = AsyncMock()
    engine.runtime = runtime
    engine.browser = old_browser
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
    runtime = make_runtime()
    runtime.is_browser_connected.return_value = False
    new_context = MagicMock()
    new_context.on = MagicMock()
    new_context.new_page = AsyncMock(return_value=MagicMock())
    new_browser = make_browser()
    new_browser.new_context = AsyncMock(return_value=new_context)
    runtime.launch_browser = AsyncMock(return_value=new_browser)
    old_browser = make_browser()
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    from app.services.browser.session import ProviderSession

    session = ProviderSession(engine, "gemini", enable_persistence=True)
    session.last_browser_generation = 1
    session._eviction_loop = AsyncMock()
    session._reaper_loop = AsyncMock()
    engine.runtime = runtime
    engine.browser = old_browser
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
    runtime = make_runtime()
    runtime.is_browser_connected.return_value = False
    new_context = MagicMock()
    new_context.on = MagicMock()
    new_context.new_page = AsyncMock(return_value=MagicMock())
    new_browser = make_browser()
    new_browser.new_context = AsyncMock(side_effect=lambda **_: (order.append("context"), new_context)[1])
    runtime.launch_browser = AsyncMock(return_value=new_browser)
    old_browser = make_browser()
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
    engine.runtime = runtime
    engine.browser = old_browser
    engine.browser_generation = 1
    engine.sessions = {"gemini": session}

    recovery = asyncio.create_task(session._do_session_recovery())
    await close_started.wait()
    replacement = asyncio.create_task(session.ensure_healthy())

    release_close.set()
    await asyncio.gather(recovery, replacement)
    await session.close_resources(save_state=False)

    assert order == ["recovery", "context"]
    assert session.last_browser_generation == 2


@pytest.mark.asyncio
async def test_shutdown_waits_for_ensure_healthy_before_closing_browser(mocker):
    engine = BrowserEngine(headless=True, is_bootstrap=True)
    runtime = make_runtime()
    context = MagicMock()
    context.is_closed.return_value = False
    context.close = AsyncMock()
    context.on = MagicMock()
    context.new_page = AsyncMock(return_value=MagicMock())
    browser = make_browser()
    browser.new_context = AsyncMock(return_value=context)
    health_started = asyncio.Event()
    release_health = asyncio.Event()

    async def block_health():
        health_started.set()
        await release_health.wait()

    engine._ensure_healthy_browser = block_health
    engine.runtime = runtime
    engine.browser = browser
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
    assert runtime.close_browser.await_count == 1
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
    runtime = make_runtime()
    context = MagicMock()
    context.is_closed.return_value = False
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_context():
        close_started.set()
        await release_close.wait()

    context.close = AsyncMock(side_effect=close_context)
    engine.runtime = runtime
    engine.browser = make_browser()
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
    assert runtime.close_browser.await_count == 1
