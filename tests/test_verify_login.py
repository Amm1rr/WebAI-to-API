import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
import verify_login


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


def _verify_login_mocks(mocker, save_results, state=None, context_cookies=None):
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

    auth_state = state or {
        "cookies": [{"name": "__Secure-1PSID", "value": "test-psid", "domain": ".google.com"}],
        "origins": [],
    }

    async def save_state(*, validate=None):
        if validate is not None:
            await validate(auth_state)
        result = save_results.pop(0)
        save_finished.set()
        return result

    session = MagicMock()
    session.state_path = "runtime/auth/gemini.json"
    session.is_alive = True
    session.context.cookies = AsyncMock(return_value=context_cookies or auth_state["cookies"])
    session.save_state = AsyncMock(side_effect=save_state)

    engine = MagicMock()
    engine.is_shutting_down = False
    engine.get_page = AsyncMock(return_value=page_wrapper)
    engine.get_session = AsyncMock(return_value=session)

    async def close(**_kwargs):
        engine.is_shutting_down = True

    engine.close = AsyncMock(side_effect=close)

    async def wait_for_completion(*args):
        await save_finished.wait()
        return "engine_shutdown"

    mocker.patch.object(
        verify_login,
        "get_browser_engine",
        AsyncMock(return_value=engine),
    )
    mocker.patch.object(
        verify_login,
        "_wait_for_completion_signal",
        AsyncMock(side_effect=wait_for_completion),
    )
    return session, page_wrapper, engine


@pytest.mark.asyncio
async def test_verify_login_closes_engine_when_get_page_fails(mocker, capsys):
    _, _, engine = _verify_login_mocks(mocker, [])
    error = RuntimeError("get page failed")
    engine.get_page = AsyncMock(side_effect=error)

    with pytest.raises(RuntimeError, match="get page failed") as exc_info:
        await verify_login.verify_login()

    assert exc_info.value is error
    engine.close.assert_awaited_once_with(save_state=False)
    assert "[SUCCESS]" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_verify_login_closes_engine_when_get_session_fails(mocker, capsys):
    _, page_wrapper, engine = _verify_login_mocks(mocker, [])
    error = RuntimeError("get session failed")
    engine.get_session = AsyncMock(side_effect=error)

    with pytest.raises(RuntimeError, match="get session failed") as exc_info:
        await verify_login.verify_login()

    assert exc_info.value is error
    page_wrapper.close.assert_awaited_once()
    engine.close.assert_awaited_once_with(save_state=False)
    assert "[SUCCESS]" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_verify_login_closes_engine_without_generic_save_when_goto_fails(mocker, capsys):
    session, page_wrapper, engine = _verify_login_mocks(mocker, [])
    error = RuntimeError("goto failed")
    page_wrapper.page.goto = AsyncMock(side_effect=error)

    with pytest.raises(RuntimeError, match="goto failed") as exc_info:
        await verify_login.verify_login()

    assert exc_info.value is error
    session.save_state.assert_not_awaited()
    page_wrapper.close.assert_awaited_once()
    engine.close.assert_awaited_once_with(save_state=False)
    assert "[SUCCESS]" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_verify_login_releases_page_when_browser_completion_signal_fires(mocker, capsys):
    session, page_wrapper, engine = _verify_login_mocks(mocker, [True, True])

    await verify_login.verify_login()

    captured = capsys.readouterr()
    assert "[SUCCESS] Shared Gemini authentication state saved atomically" in captured.out
    assert "[FINAL SAVE] Verified persistent state saved" in captured.out
    assert "Manual bootstrap utility successfully completed" in captured.out
    engine.get_page.assert_called_once_with("gemini", enable_persistence=True)
    engine.get_session.assert_called_once_with("gemini", enable_persistence=True)
    page_wrapper.close.assert_called_once()
    engine.close.assert_awaited_once_with(save_state=False)
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
    engine.close.assert_awaited_once_with(save_state=False)
    session.save_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_login_reports_final_save_failure_after_cleanup(mocker, capsys):
    session, page_wrapper, engine = _verify_login_mocks(mocker, [True, False])

    with pytest.raises(RuntimeError, match="could not be persisted"):
        await verify_login.verify_login()

    captured = capsys.readouterr()
    assert "[SUCCESS] Shared Gemini authentication state saved atomically" in captured.out
    assert "[FINAL SAVE]" not in captured.out
    assert "[ERROR] Authenticated state could not be persisted" in captured.err
    page_wrapper.close.assert_called_once()
    engine.close.assert_awaited_once_with(save_state=False)
    assert session.save_state.await_count == 2


@pytest.mark.asyncio
async def test_verify_login_rejects_ui_only_auth_without_cookie_values(mocker, capsys):
    session, page_wrapper, engine = _verify_login_mocks(
        mocker,
        [True],
        state={"cookies": [], "origins": []},
    )

    with pytest.raises(RuntimeError, match="shared WebAPI authentication material"):
        await verify_login.verify_login()

    captured = capsys.readouterr()
    assert "[SUCCESS]" not in captured.out
    assert "shared WebAPI authentication material" in captured.err
    assert "test-psid" not in captured.err
    session.save_state.assert_awaited_once()
    page_wrapper.close.assert_awaited_once()
    engine.close.assert_awaited_once_with(save_state=False)


@pytest.mark.asyncio
async def test_verify_login_rejects_partitioned_context_psid(mocker, capsys):
    state = {
        "cookies": [{"name": "__Secure-1PSID", "value": "test-psid", "domain": ".google.com", "path": "/"}],
        "origins": [],
    }
    context_cookies = [{
        "name": "__Secure-1PSID",
        "value": "test-psid",
        "domain": ".google.com",
        "path": "/",
        "partitionKey": "https://gemini.google.com",
    }]
    session, page_wrapper, engine = _verify_login_mocks(
        mocker, [True], state=state, context_cookies=context_cookies
    )

    with pytest.raises(RuntimeError, match="shared WebAPI authentication material"):
        await verify_login.verify_login()

    captured = capsys.readouterr()
    assert "[SUCCESS]" not in captured.out
    assert "test-psid" not in captured.err
    session.save_state.assert_awaited_once()
    page_wrapper.close.assert_awaited_once()
    engine.close.assert_awaited_once_with(save_state=False)


@pytest.mark.asyncio
async def test_verify_login_preserves_engine_cleanup_when_page_close_fails(mocker):
    _, page_wrapper, engine = _verify_login_mocks(mocker, [True, True])
    page_wrapper.close.side_effect = RuntimeError("page close failed")

    with pytest.raises(RuntimeError, match="page close failed"):
        await verify_login.verify_login()

    engine.close.assert_awaited_once_with(save_state=False)
