import pytest
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

