from unittest.mock import AsyncMock, MagicMock
import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError

from app.services.browser.errors import BrowserDisconnectedError, ConversationBusyError
from app.services.browser.session import ProviderSession
from app.services.browser.tab import TabStatus
from app.services.providers.gemini.auth_selector import GeminiAuthCandidate


def auth_candidate(auth_data, source_type="gemini_config", is_legacy=False):
    return GeminiAuthCandidate(
        source_name="[Gemini] config",
        source_type=source_type,
        auth_data=auth_data,
        is_legacy=is_legacy,
        supports_webapi_cookie_auth=True,
        supports_playwright_storage=(source_type == "json_store"),
        migration_needed=is_legacy,
    )


def make_engine(is_bootstrap=False):
    context = MagicMock()
    context.on = MagicMock()
    context.new_page = AsyncMock(return_value=MagicMock())

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    engine = MagicMock()
    engine.max_pages = 2
    engine.browser = browser
    engine.browser_generation = 3
    engine.is_shutting_down = False
    engine.is_bootstrap = is_bootstrap
    return engine, browser


@pytest.mark.asyncio
async def test_gemini_session_setup_uses_selector_storage_candidate(mocker):
    auth_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "psid", "domain": ".google.com"},
        ],
        "origins": [],
    }
    storage_state = {"cookies": auth_data["cookies"], "origins": []}
    # Use json_store to ensure supports_playwright_storage is True
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([auth_candidate(auth_data, source_type="json_store")]),
    )
    translate = mocker.patch(
        "app.services.browser.auth_loader.GeminiAuthStateLoader.translate_to_playwright",
        return_value=storage_state,
    )
    load_fallback = mocker.patch(
        "app.services.browser.auth_loader.GeminiAuthStateLoader.load_auth_state_with_fallback"
    )
    browser_extractor = mocker.patch("app.utils.browser.get_cookie_from_browser")
    client_factory = mocker.patch("app.services.providers.gemini.client.MyGeminiClient")
    engine, browser = make_engine()
    session = ProviderSession(engine, "gemini")
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())

    await session._setup()

    translate.assert_called_once_with(auth_data)
    assert browser.new_context.call_args.kwargs["storage_state"] == storage_state
    load_fallback.assert_not_called()
    browser_extractor.assert_not_called()
    client_factory.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_session_setup_uses_json_candidate_for_storage(mocker):
    json_auth = {
        "cookies": [],
        "origins": [{"origin": "https://gemini.google.com", "localStorage": []}],
    }
    storage_state = {"cookies": [], "origins": json_auth["origins"]}
    candidate = auth_candidate(json_auth, source_type="json_store")
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([candidate]),
    )
    mocker.patch(
        "app.services.browser.auth_loader.GeminiAuthStateLoader.translate_to_playwright",
        return_value=storage_state,
    )
    engine, browser = make_engine()
    session = ProviderSession(engine, "gemini")
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())

    await session._setup()

    assert browser.new_context.call_args.kwargs["storage_state"] == storage_state


@pytest.mark.asyncio
async def test_gemini_setup_fails_without_auth(mocker):
    # Mock engine and browser
    engine, browser = make_engine()
    
    # Mock GeminiAuthSelector to return no Playwright candidate
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )
    
    session = ProviderSession(engine, "gemini")
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())
    
    with pytest.raises(RuntimeError) as excinfo:
        await session._setup()
    
    assert "Gemini Playwright backend requires a valid storage state" in str(excinfo.value)
    assert "python verify_login.py" in str(excinfo.value)


@pytest.mark.asyncio
async def test_gemini_bootstrap_setup_allows_missing_auth_with_persistence(mocker):
    engine, browser = make_engine(is_bootstrap=True)
    selector = mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    session = ProviderSession(engine, "gemini", enable_persistence=True)
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())

    await session._setup()

    selector.assert_called_once()
    assert "storage_state" not in browser.new_context.call_args.kwargs


@pytest.mark.asyncio
async def test_gemini_bootstrap_setup_uses_existing_storage_candidate(mocker):
    auth_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "psid", "domain": ".google.com"},
        ],
        "origins": [],
    }
    storage_state = {"cookies": auth_data["cookies"], "origins": []}
    engine, browser = make_engine(is_bootstrap=True)
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([auth_candidate(auth_data, source_type="json_store")]),
    )
    translate = mocker.patch(
        "app.services.browser.auth_loader.GeminiAuthStateLoader.translate_to_playwright",
        return_value=storage_state,
    )

    session = ProviderSession(engine, "gemini", enable_persistence=True)
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())

    await session._setup()

    translate.assert_called_once_with(auth_data)
    assert browser.new_context.call_args.kwargs["storage_state"] == storage_state


@pytest.mark.asyncio
async def test_gemini_setup_enable_persistence_without_bootstrap_still_requires_auth(mocker):
    engine, browser = make_engine(is_bootstrap=False)
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    session = ProviderSession(engine, "gemini", enable_persistence=True)
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())

    with pytest.raises(RuntimeError) as excinfo:
        await session._setup()

    assert "Gemini Playwright backend requires a valid storage state" in str(excinfo.value)


@pytest.mark.asyncio
async def test_gemini_bootstrap_without_persistence_still_requires_auth(mocker):
    engine, browser = make_engine(is_bootstrap=True)
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([]),
    )

    session = ProviderSession(engine, "gemini", enable_persistence=False)
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())

    with pytest.raises(RuntimeError) as excinfo:
        await session._setup()

    assert "Gemini Playwright backend requires a valid storage state" in str(excinfo.value)


@pytest.mark.asyncio
async def test_close_resources_is_idempotent_for_context(mocker):
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    context = MagicMock()
    context.is_closed.return_value = False
    context.close = AsyncMock()
    session.context = context

    await session.close_resources(save_state=False)
    await session.close_resources(save_state=False)

    assert session.context is None
    context.close.assert_awaited_once()


def test_active_request_handles_signal_and_abort_once():
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    signals = []
    aborts = []

    session.register_request_abort("request-1", signals.append, lambda: aborts.append("request-1"))
    session.register_request_abort("request-2", signals.append, lambda: aborts.append("request-2"))
    error = BrowserDisconnectedError("browser disconnected")

    session.signal_active_requests(lambda: BrowserDisconnectedError(str(error)))
    session.abort_active_requests()
    session.unregister_request_abort("request-1")
    session.abort_active_requests()

    assert signals[0] is not signals[1]
    assert [type(value) for value in signals] == [BrowserDisconnectedError, BrowserDisconnectedError]
    assert [str(value) for value in signals] == [str(error), str(error)]
    assert aborts == ["request-1", "request-2", "request-2"]


@pytest.mark.asyncio
async def test_terminal_cleanup_drains_orphan_tasks_created_by_purge():
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    tab = MagicMock()
    tab.status = TabStatus.LEASED
    tab.lease_token = "token"
    tab.invalidate.side_effect = lambda: setattr(tab, "status", TabStatus.INVALIDATING)
    session.conversation_registry["conversation"] = tab

    await session.close_resources(save_state=False)

    assert not session._orphan_cleanup_tasks
    assert tab._cleanup_task is None


@pytest.mark.asyncio
async def test_terminal_cleanup_cancels_pending_recovery_task():
    engine, _ = make_engine()
    engine.is_shutting_down = True
    session = ProviderSession(engine, "test_provider")
    started = asyncio.Event()
    release = asyncio.Event()

    async def pending_recovery():
        started.set()
        await release.wait()

    session._recovery_task = asyncio.create_task(pending_recovery())
    await started.wait()
    await session.close_resources(save_state=False)

    assert session._recovery_task is None


@pytest.mark.asyncio
async def test_lifecycle_task_exception_is_retrieved(caplog):
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")

    async def fail_task():
        raise RuntimeError("lifecycle task failed")

    task = asyncio.create_task(fail_task())
    session._track_lifecycle_task(task, "context-close")

    with pytest.raises(RuntimeError, match="lifecycle task failed"):
        await task

    assert not session._lifecycle_tasks
    assert any("context-close task failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_close_resources_serializes_concurrent_cleanup_and_detaches_context():
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    context = MagicMock()
    context.is_closed.return_value = False

    async def close_context():
        close_started.set()
        await release_close.wait()

    context.close = AsyncMock(side_effect=close_context)
    session.context = context

    first = asyncio.create_task(session.close_resources(save_state=False))
    await close_started.wait()
    assert session.context is None

    second = asyncio.create_task(session.close_resources(save_state=False))
    await asyncio.sleep(0)
    assert not second.done()

    release_close.set()
    await asyncio.gather(first, second)

    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_resources_skips_already_closed_context(caplog):
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    context = MagicMock()
    context.is_closed.return_value = True
    context.close = AsyncMock()
    session.context = context

    await session.close_resources(save_state=False)

    assert session.context is None
    context.close.assert_not_awaited()
    assert "Failed to close browser context" not in caplog.text


@pytest.mark.asyncio
async def test_close_resources_tolerates_closed_public_playwright_error(caplog):
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    context = MagicMock()
    context.is_closed.side_effect = [False, True]
    context.close = AsyncMock(side_effect=PlaywrightError("context closed during close"))
    session.context = context

    await session.close_resources(save_state=False)
    await session.close_resources(save_state=False)

    assert session.context is None
    context.close.assert_awaited_once()
    assert "Failed to close browser context" not in caplog.text


@pytest.mark.asyncio
async def test_close_resources_logs_open_context_public_playwright_error(caplog):
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    context = MagicMock()
    context.is_closed.side_effect = [False, False]
    context.close = AsyncMock(side_effect=PlaywrightError("context close failed"))
    session.context = context

    await session.close_resources(save_state=False)

    assert session.context is None
    assert "Failed to close browser context: context close failed" in caplog.text


@pytest.mark.asyncio
async def test_close_resources_preserves_close_error_when_state_inspection_fails(caplog):
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    context = MagicMock()
    context.is_closed.side_effect = [False, RuntimeError("inspection failed")]
    context.close = AsyncMock(side_effect=PlaywrightError("original close failure"))
    session.context = context

    await session.close_resources(save_state=False)

    assert session.context is None
    assert "original close failure" in caplog.text
    assert "inspection failed" in caplog.text


@pytest.mark.asyncio
async def test_close_resources_logs_unexpected_context_close_error(caplog):
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    context = MagicMock()
    context.is_closed.return_value = False
    context.close = AsyncMock(side_effect=RuntimeError("unexpected close failure"))
    session.context = context

    await session.close_resources(save_state=False)

    assert session.context is None
    assert "Failed to close browser context: unexpected close failure" in caplog.text


@pytest.mark.asyncio
async def test_acquire_lease_pre_header_busy_conversation_rejected():
    """Verifies pre-header rejection: acquiring a lease for a conversation that
    is already active under a different request raises ConversationBusyError.
    """
    engine, _ = make_engine()
    session = ProviderSession(engine, "test_provider")
    session.active_conversations["existing_cid"] = "req_owner"

    with pytest.raises(ConversationBusyError) as excinfo:
        await session.acquire_lease(conversation_id="existing_cid", request_id="req_new")

    assert "busy" in str(excinfo.value)
    assert session.active_conversations["existing_cid"] == "req_owner"
