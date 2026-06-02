import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


async def _get(path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(path)


async def _openapi_paths():
    response = await _get("/openapi.json")
    assert response.status_code == 200
    return response.json()["paths"]


def _runtime_status_payload():
    return {
        "engine": {
            "status": "RUNNING",
            "browser_connected": True,
            "browser_generation": 7,
            "is_bootstrap": False,
        },
        "sessions": {
            "gemini": {
                "is_alive": True,
                "metrics": {"active": 1},
                "is_recovering": False,
            }
        },
        "auth": {},
    }


def _auth_status_payload():
    return {
        "timestamp": "2026-06-02T00:00:00Z",
        "login_state": "IDLE",
        "gemini_webapi": {"status": "AUTHENTICATED"},
        "playwright": {
            "status": "VALID_SESSION",
            "auth_state_file": "runtime/auth/gemini.json",
            "last_validated": "2026-06-02T00:00:00Z",
            "validation_details": "Cached test validation.",
        },
    }


@pytest.mark.asyncio
async def test_ui_index_returns_html():
    response = await _get("/ui")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Runtime Overview" in response.text
    assert "/ui/static/js/htmx.min.js" in response.text


@pytest.mark.asyncio
async def test_ui_status_returns_html_and_uses_htmx_refresh(mocker):
    runtime_status = mocker.patch(
        "app.endpoints.ui.runtime_status",
        return_value=_runtime_status_payload(),
    )

    response = await _get("/ui/status")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Runtime Status" in response.text
    assert 'hx-get="/ui/status/panel"' in response.text
    assert "RUNNING" in response.text
    runtime_status.assert_called_once()


@pytest.mark.asyncio
async def test_ui_status_panel_returns_fragment(mocker):
    mocker.patch(
        "app.endpoints.ui.runtime_status",
        return_value=_runtime_status_payload(),
    )

    response = await _get("/ui/status/panel")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Engine" in response.text
    assert "gemini" in response.text


@pytest.mark.asyncio
async def test_ui_auth_returns_html_and_uses_htmx_refresh(mocker):
    get_auth_status = mocker.patch(
        "app.endpoints.ui.get_auth_status",
        return_value=_auth_status_payload(),
    )

    response = await _get("/ui/auth")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Authentication Status" in response.text
    assert 'hx-get="/ui/auth/panel"' in response.text
    assert "VALID_SESSION" in response.text
    get_auth_status.assert_called_once_with(refresh=False)


@pytest.mark.asyncio
async def test_ui_auth_panel_returns_fragment(mocker):
    mocker.patch(
        "app.endpoints.ui.get_auth_status",
        return_value=_auth_status_payload(),
    )

    response = await _get("/ui/auth/panel")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Provider Auth" in response.text
    assert "AUTHENTICATED" in response.text


@pytest.mark.asyncio
async def test_ui_models_returns_html(mocker):
    list_models = mocker.patch(
        "app.endpoints.ui.list_models",
        return_value={
            "object": "list",
            "data": [
                {
                    "id": "gemini/gemini-3-flash",
                    "object": "model",
                    "owned_by": "gemini",
                }
            ],
        },
    )

    response = await _get("/ui/models")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Available Models" in response.text
    assert "gemini/gemini-3-flash" in response.text
    list_models.assert_called_once()


@pytest.mark.asyncio
async def test_ui_static_assets_are_served():
    css_response = await _get("/ui/static/css/dashboard.css")
    htmx_response = await _get("/ui/static/js/htmx.min.js")

    assert css_response.status_code == 200
    assert "text/css" in css_response.headers["content-type"]
    assert htmx_response.status_code == 200
    assert "javascript" in htmx_response.headers["content-type"]
    assert "htmx" in htmx_response.text.lower()


@pytest.mark.asyncio
async def test_ui_routes_are_excluded_from_openapi():
    paths = await _openapi_paths()
    assert "/ui" not in paths
    assert "/ui/status" not in paths
    assert "/ui/auth" not in paths
    assert "/ui/models" not in paths
