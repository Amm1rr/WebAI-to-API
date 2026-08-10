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


@pytest.mark.asyncio
async def test_verify_login_releases_page_when_browser_completion_signal_fires(mocker):
    page = MagicMock()
    page.goto = AsyncMock()

    page_wrapper = MagicMock()
    page_wrapper.page = page
    page_wrapper.close = AsyncMock()

    session = MagicMock()
    session.state_path = "runtime/auth/gemini.json"
    session.is_alive = True
    session.save_state = AsyncMock()

    engine = MagicMock()
    engine.get_page = AsyncMock(return_value=page_wrapper)
    engine.get_session = AsyncMock(return_value=session)
    engine.close = AsyncMock()

    mocker.patch.object(
        verify_login,
        "get_browser_engine",
        AsyncMock(return_value=AsyncEngineContext(engine)),
    )
    mocker.patch.object(
        verify_login,
        "_wait_for_completion_signal",
        AsyncMock(return_value="engine_shutdown"),
    )

    await verify_login.verify_login()

    engine.get_page.assert_called_once_with("gemini", enable_persistence=True)
    engine.get_session.assert_called_once_with("gemini", enable_persistence=True)
    page_wrapper.close.assert_called_once()
    engine.close.assert_called_once()
