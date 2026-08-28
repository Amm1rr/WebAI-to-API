import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
import verify_login


class AsyncEngineContext:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        return self.engine

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.engine.close()
        return False


async def never_enter():
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_completion_signal_returns_when_engine_starts_shutdown():
    engine = MagicMock()
    engine.is_shutting_down = False

    page = MagicMock()
    page.is_closed.return_value = False

    session = MagicMock()
    session.is_alive = True

    async def trigger_shutdown():
        await asyncio.sleep(0.01)
        engine.is_shutting_down = True

    shutdown_task = asyncio.create_task(trigger_shutdown())
    try:
        result = await verify_login._wait_for_completion_signal(
            engine,
            page,
            session,
            stdin_waiter=never_enter,
        )
    finally:
        await shutdown_task

    assert result == "engine_shutdown"


@pytest.mark.asyncio
async def test_completion_signal_returns_when_page_closes():
    engine = MagicMock()
    engine.is_shutting_down = False

    page = MagicMock()
    page.is_closed.return_value = True

    session = MagicMock()
    session.is_alive = True

    result = await verify_login._wait_for_completion_signal(
        engine,
        page,
        session,
        stdin_waiter=never_enter,
    )

    assert result == "page_closed"


def _verify_login_mocks(mocker, save_results):
    page = MagicMock()
    page.goto = AsyncMock()
    visible = AsyncMock(return_value=True)
    locator = MagicMock()
    locator.first.is_visible = visible
    page.locator = MagicMock(return_value=locator)

    page_wrapper = MagicMock()
    page_wrapper.page = page
    page_wrapper.close = AsyncMock()

    save_finished = asyncio.Event()

    async def save_state():
        result = save_results.pop(0)
        save_finished.set()
        return result

    session = MagicMock()
    session.state_path = "runtime/auth/gemini.json"
    session.is_alive = True
    session.save_state = AsyncMock(side_effect=save_state)

    engine = MagicMock()
    engine.get_page = AsyncMock(return_value=page_wrapper)
    engine.get_session = AsyncMock(return_value=session)
    engine.close = AsyncMock()

    async def wait_for_completion(*args):
        await save_finished.wait()
        return "engine_shutdown"

    mocker.patch.object(
        verify_login,
        "get_browser_engine",
        AsyncMock(return_value=AsyncEngineContext(engine)),
    )
    mocker.patch.object(
        verify_login,
        "_wait_for_completion_signal",
        AsyncMock(side_effect=wait_for_completion),
    )
    return session, page_wrapper, engine


@pytest.mark.asyncio
async def test_verify_login_releases_page_when_browser_completion_signal_fires(mocker, capsys):
    session, page_wrapper, engine = _verify_login_mocks(mocker, [True, True])

    await verify_login.verify_login()

    captured = capsys.readouterr()
    assert "[SUCCESS] Login detected! State saved atomically" in captured.out
    assert "[FINAL SAVE] Verified persistent state saved" in captured.out
    assert "Manual bootstrap utility successfully completed" in captured.out
    engine.get_page.assert_called_once_with("gemini", enable_persistence=True)
    engine.get_session.assert_called_once_with("gemini", enable_persistence=True)
    page_wrapper.close.assert_called_once()
    engine.close.assert_called_once()
    assert session.save_state.await_count == 2


@pytest.mark.asyncio
async def test_verify_login_reports_success_only_after_initial_state_persists(mocker, capsys):
    session, page_wrapper, engine = _verify_login_mocks(mocker, [False])

    with pytest.raises(RuntimeError, match="could not be persisted"):
        await verify_login.verify_login()

    captured = capsys.readouterr()
    assert "[SUCCESS]" not in captured.out
    assert "[ERROR] Authenticated state could not be persisted" in captured.err
    page_wrapper.close.assert_called_once()
    engine.close.assert_called_once()
    session.save_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_login_reports_final_save_failure_after_cleanup(mocker, capsys):
    session, page_wrapper, engine = _verify_login_mocks(mocker, [True, False])

    with pytest.raises(RuntimeError, match="could not be persisted"):
        await verify_login.verify_login()

    captured = capsys.readouterr()
    assert "[SUCCESS] Login detected! State saved atomically" in captured.out
    assert "[FINAL SAVE]" not in captured.out
    assert "[ERROR] Authenticated state could not be persisted" in captured.err
    page_wrapper.close.assert_called_once()
    engine.close.assert_called_once()
    assert session.save_state.await_count == 2
