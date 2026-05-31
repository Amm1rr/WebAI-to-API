import pytest
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.browser.auth_manager import get_auth_manager, LoginState, AuthStatus

@pytest.mark.asyncio
async def test_get_auth_status_endpoint(mocker):
    """Verify /v1/auth/status returns correct cached status structure."""
    auth_mgr = get_auth_manager()
    auth_mgr.coordination_lock.release()
    auth_mgr._cached_playwright_status = AuthStatus.VALID_SESSION
    auth_mgr._cached_webapi_status = AuthStatus.AUTHENTICATED
    auth_mgr._last_validated = 1000.0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/auth/status")

    assert response.status_code == 200
    data = response.json()
    assert "timestamp" in data
    assert data["login_state"] == "IDLE"
    assert data["gemini_webapi"]["status"] == "AUTHENTICATED"
    assert data["playwright"]["status"] == "VALID_SESSION"

@pytest.mark.asyncio
async def test_trigger_auth_login_endpoint_success(mocker):
    """Verify /v1/auth/login triggers successfully and returns 202."""
    auth_mgr = get_auth_manager()
    mocker.patch.object(auth_mgr, "start_login")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/auth/login")

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "LOGIN_IN_PROGRESS"
    auth_mgr.start_login.assert_called_once()

@pytest.mark.asyncio
async def test_trigger_auth_login_endpoint_conflict(mocker):
    """Verify /v1/auth/login returns 409 when login is already in progress."""
    auth_mgr = get_auth_manager()
    mocker.patch.object(auth_mgr, "start_login", side_effect=ValueError("Authentication in progress."))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/auth/login")

    assert response.status_code == 409
    assert response.json()["detail"] == "Authentication in progress."

@pytest.mark.asyncio
async def test_trigger_auth_login_endpoint_headless_unsupported(mocker):
    """Verify /v1/auth/login returns 400 when triggered in headless environment."""
    auth_mgr = get_auth_manager()
    mocker.patch.object(auth_mgr, "start_login", side_effect=RuntimeError("Headful interactive sign-in is unsupported in this headless container environment."))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/auth/login")

    assert response.status_code == 400
    assert "headless container environment" in response.json()["detail"]


def test_in_memory_auth_lock():
    """Verify InMemoryAuthLock behavior: acquire, release, is_locked."""
    from app.services.browser.auth_manager import InMemoryAuthLock

    lock = InMemoryAuthLock()
    assert not lock.is_locked()

    # First acquire should succeed
    assert lock.acquire()
    assert lock.is_locked()

    # Second acquire under same lock should fail
    assert not lock.acquire()
    assert lock.is_locked()

    # Release should reset state
    lock.release()
    assert not lock.is_locked()

    # Redundant release should be safe
    lock.release()
    assert not lock.is_locked()


def test_auth_manager_multi_worker_warning(mocker):
    """Verify that AuthManager logs a warning when multiple workers are configured under in_memory."""
    import os
    from app.services.browser.auth_manager import AuthManager
    from app.logger import logger

    # Spy on logger.warning
    spy_warning = mocker.spy(logger, "warning")

    # Set environment variables for multiple workers
    mocker.patch.dict(os.environ, {"WEB_CONCURRENCY": "3"})

    # Reset any existing AuthManager instance initialization parameters
    # to trigger the warning logic
    try:
        # Re-trigger _check_multi_worker_warning with "in_memory"
        auth_mgr = AuthManager.get_instance()
        auth_mgr._check_multi_worker_warning("in_memory")
        
        # Check that warning was logged
        warning_calls = [call.args[0] for call in spy_warning.mock_calls if call.args]
        warning_logged = any("Multiple workers detected" in msg for msg in warning_calls)
        assert warning_logged, f"Expected multi-worker warning not found in calls: {warning_calls}"
    finally:
        pass


@pytest.mark.asyncio
async def test_run_login_flow_success(mocker):
    """Verify run_login_flow detects authenticated chat after traversing sign-in and guest states."""
    from app.services.browser.auth_manager import get_auth_manager
    from app.services.providers.gemini.auth import GeminiAuthStrategy
    auth_mgr = get_auth_manager()
    auth_mgr.set_strategy(GeminiAuthStrategy())

    # Mock display check
    mocker.patch.object(auth_mgr, "_check_display_available", return_value=True)

    # Mock browser engine primitives
    mock_page = MagicMock()
    mock_page.is_closed.return_value = False
    
    # Simulate page state transitions across 3 polls:
    # Poll 0: on accounts.google.com (sign_in_page_detected)
    # Poll 1: input visible but sign_in_button visible (waiting_for_user_login)
    # Poll 2: input visible, sign_in_button absent, url on gemini (authenticated_chat_detected)
    urls = [
        "https://accounts.google.com/signin/v2/identifier",
        "https://gemini.google.com/app",
        "https://gemini.google.com/app"
    ]
    mock_page.url = urls[0]
    
    mock_page.goto = AsyncMock()

    # Define selectors locally matching the ones in run_login_flow
    SIGN_IN_SELECTORS = [
        'a[href*="accounts.google.com"]',
        'a:has-text("Sign in")',
        'button:has-text("Sign in")',
        'a[aria-label*="Sign in"]',
        '.sign-in-button'
    ]

    AUTHENTICATED_SELECTORS = [
        'a[href*="SignOutOptions"]',
        'a[href*="myaccount.google.com"]',
        'img[src*="googleusercontent.com"]',
        '[aria-label*="Google Account"]'
    ]

    # Mock locator function to return specific visibility depending on page url
    def mock_locator_fn(selector):
        loc = MagicMock()
        first_mock = AsyncMock()
        
        async def is_visible():
            current_url = mock_page.url
            if "accounts.google.com" in current_url:
                if selector in SIGN_IN_SELECTORS:
                    return True
                return False
            elif current_url == "https://gemini.google.com/app" and poll_count == 1:
                # Poll 1 (guest)
                if selector == 'div[contenteditable="true"][role="textbox"], textarea.gds-body-l, textarea[placeholder*="Gemini"]':
                    return True
                if selector in SIGN_IN_SELECTORS:
                    return True
                return False
            else:
                # Poll 2 (auth)
                if selector == 'div[contenteditable="true"][role="textbox"], textarea.gds-body-l, textarea[placeholder*="Gemini"]':
                    return True
                if selector in AUTHENTICATED_SELECTORS:
                    return True
                return False
                
        first_mock.is_visible = AsyncMock(side_effect=is_visible)
        loc.first = first_mock
        return loc

    mock_page.locator = MagicMock(side_effect=mock_locator_fn)

    # Use sleep mock to progress states
    poll_count = 0
    async def mock_sleep(seconds):
        nonlocal poll_count
        poll_count += 1
        if poll_count < len(urls):
            mock_page.url = urls[poll_count]

    mocker.patch('asyncio.sleep', AsyncMock(side_effect=mock_sleep))

    mock_page_wrapper = MagicMock()
    mock_page_wrapper.page = mock_page
    mock_page_wrapper.close = AsyncMock()

    mock_session = AsyncMock()
    mock_session.save_state = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.get_page = AsyncMock(return_value=mock_page_wrapper)
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    mock_engine.__aenter__ = AsyncMock(return_value=mock_engine)
    mock_engine.__aexit__ = AsyncMock(return_value=False)  # DO NOT suppress exceptions

    mocker.patch('app.services.browser.engine.get_browser_engine', return_value=mock_engine)

    await auth_mgr.run_login_flow()

    mock_session.save_state.assert_called_once()
    mock_page_wrapper.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_login_flow_user_closed_window(mocker):
    """Verify run_login_flow raises RuntimeError when window is closed by user."""
    from app.services.browser.auth_manager import get_auth_manager
    from app.services.providers.gemini.auth import GeminiAuthStrategy
    auth_mgr = get_auth_manager()
    auth_mgr.set_strategy(GeminiAuthStrategy())

    mocker.patch.object(auth_mgr, "_check_display_available", return_value=True)

    mock_page = MagicMock()
    mock_page.is_closed.return_value = True
    mock_page.goto = AsyncMock()

    mock_page_wrapper = MagicMock()
    mock_page_wrapper.page = mock_page
    mock_page_wrapper.close = AsyncMock()

    mock_session = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.get_page = AsyncMock(return_value=mock_page_wrapper)
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    mock_engine.__aenter__ = AsyncMock(return_value=mock_engine)
    mock_engine.__aexit__ = AsyncMock(return_value=False)  # DO NOT suppress exceptions

    mocker.patch('app.services.browser.engine.get_browser_engine', return_value=mock_engine)
    mocker.patch('asyncio.sleep', AsyncMock())

    with pytest.raises(RuntimeError, match="Interactive sign-in was closed by user"):
        await auth_mgr.run_login_flow()


@pytest.mark.asyncio
async def test_run_login_flow_unexpected_exception_re_raised(mocker):
    """Verify run_login_flow re-raises unexpected exceptions during polling."""
    from app.services.browser.auth_manager import get_auth_manager
    from app.services.providers.gemini.auth import GeminiAuthStrategy
    auth_mgr = get_auth_manager()
    auth_mgr.set_strategy(GeminiAuthStrategy())

    mocker.patch.object(auth_mgr, "_check_display_available", return_value=True)

    mock_page = MagicMock()
    mock_page.is_closed.return_value = False
    # Raise an unexpected error during navigation or polling
    mock_page.goto = AsyncMock(side_effect=ValueError("Unexpected internal error"))

    mock_page_wrapper = MagicMock()
    mock_page_wrapper.page = mock_page
    mock_page_wrapper.close = AsyncMock()

    mock_session = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.get_page = AsyncMock(return_value=mock_page_wrapper)
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    mock_engine.__aenter__ = AsyncMock(return_value=mock_engine)
    mock_engine.__aexit__ = AsyncMock(return_value=False)

    mocker.patch('app.services.browser.engine.get_browser_engine', return_value=mock_engine)

    with pytest.raises(ValueError, match="Unexpected internal error"):
        await auth_mgr.run_login_flow()


