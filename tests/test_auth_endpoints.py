import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.browser.auth_manager import get_auth_manager, LoginState, AuthStatus

@pytest.mark.asyncio
async def test_get_auth_status_endpoint(mocker):
    """Verify /v1/auth/status returns correct cached status structure."""
    auth_mgr = get_auth_manager()
    auth_mgr.login_state = LoginState.IDLE
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
