import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from app.main import app
from app.services.browser.auth_types import AuthStatus

client = TestClient(app)

@pytest.fixture
def mock_engine_instance():
    with patch("app.services.browser.engine.BrowserEngine._instance") as mock:
        engine = MagicMock()
        engine.is_shutting_down = False
        engine.browser = MagicMock()
        engine.runtime.is_browser_connected.return_value = True
        engine.browser_generation = 1
        engine.is_bootstrap = False
        engine.sessions = {}

        # Canonical accessor reads engine.BrowserEngine._instance at call time.
        import app.services.browser.engine as engine_module
        old_instance = engine_module.BrowserEngine._instance
        engine_module.BrowserEngine._instance = engine
        yield engine
        engine_module.BrowserEngine._instance = old_instance

@pytest.fixture
def mock_auth_mgr():
    with patch("app.endpoints.system.get_auth_manager") as mock:
        auth_mgr = MagicMock()
        auth_mgr.get_status.return_value = {
            "playwright": {"status": AuthStatus.VALID_SESSION},
            "webapi": {"status": AuthStatus.AUTHENTICATED}
        }
        mock.return_value = auth_mgr
        yield auth_mgr

def test_health_200_uninitialized():
    # If BrowserEngine._instance is None, /health should still be 200
    with patch("app.services.browser.engine.BrowserEngine._instance", None):
        response = client.get("/health")
        assert response.status_code == 200

def test_health_200_initialized(mock_engine_instance):
    response = client.get("/health")
    assert response.status_code == 200

def test_health_503_shutting_down(mock_engine_instance):
    mock_engine_instance.is_shutting_down = True
    response = client.get("/health")
    assert response.status_code == 503

def test_ready_200(mock_engine_instance):
    # Setup at least one alive session
    mock_session = MagicMock()
    mock_session.is_alive = True
    mock_engine_instance.sessions = {"gemini": mock_session}
    
    response = client.get("/ready")
    assert response.status_code == 200

def test_ready_503_uninitialized():
    with patch("app.services.browser.engine.BrowserEngine._instance", None):
        response = client.get("/ready")
        assert response.status_code == 503

def test_ready_503_no_sessions(mock_engine_instance):
    mock_engine_instance.sessions = {}
    response = client.get("/ready")
    assert response.status_code == 503

def test_ready_503_session_dead(mock_engine_instance):
    mock_session = MagicMock()
    mock_session.is_alive = False
    mock_engine_instance.sessions = {"gemini": mock_session}
    
    response = client.get("/ready")
    assert response.status_code == 503

def test_ready_503_browser_disconnected(mock_engine_instance):
    mock_engine_instance.runtime.is_browser_connected.return_value = False
    
    # Even if session is alive, if browser is disconnected, it's not ready
    mock_session = MagicMock()
    mock_session.is_alive = True
    mock_engine_instance.sessions = {"gemini": mock_session}
    
    response = client.get("/ready")
    assert response.status_code == 503

def test_ready_ignoring_auth(mock_engine_instance, mock_auth_mgr):
    # Auth expired but structural runtime is healthy
    mock_auth_mgr.get_status.return_value["playwright"]["status"] = AuthStatus.EXPIRED_SESSION
    
    mock_session = MagicMock()
    mock_session.is_alive = True
    mock_engine_instance.sessions = {"gemini": mock_session}
    
    response = client.get("/ready")
    assert response.status_code == 200

def test_runtime_status_diagnostics(mock_engine_instance, mock_auth_mgr):
    mock_session = MagicMock()
    mock_session.is_alive = True
    mock_session.metrics = {"test_metric": 123}
    mock_session._recovery_task = None
    mock_engine_instance.sessions = {"gemini": mock_session}
    
    response = client.get("/v1/runtime/status")
    assert response.status_code == 200
    data = response.json()
    
    assert data["engine"]["status"] == "RUNNING"
    assert data["engine"]["browser_connected"] is True
    assert data["engine"]["browser_generation"] == 1
    assert data["sessions"]["gemini"]["is_alive"] is True
    assert data["sessions"]["gemini"]["metrics"]["test_metric"] == 123
    assert data["auth"]["playwright"]["status"] == AuthStatus.VALID_SESSION

def test_runtime_status_uninitialized(mock_auth_mgr):
    with patch("app.services.browser.engine.BrowserEngine._instance", None):
        response = client.get("/v1/runtime/status")
        assert response.status_code == 200
        data = response.json()
        assert data["engine"]["status"] == "NOT_INITIALIZED"


def test_favicon_returns_200_with_icon_content_type():
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert len(response.content) > 0
    content_type = response.headers.get("content-type", "").lower()
    # Starlette/FileResponse uses image/x-icon; allow framework-equivalent
    assert "icon" in content_type or "x-icon" in content_type
    assert "image/" in content_type


def test_favicon_not_in_openapi():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/favicon.ico" not in paths
    # existing routes remain exposed
    assert "/health" in paths
    assert "/ready" in paths
    assert "/v1/runtime/status" in paths


def test_favicon_does_not_affect_dashboard_and_health():
    health = client.get("/health")
    assert health.status_code in (200, 503)
    dashboard = client.get("/ui")
    assert dashboard.status_code == 200
    assert 'rel="icon" href="/favicon.ico"' in dashboard.text


def test_favicon_file_exists_on_disk():
    from pathlib import Path

    favicon_path = Path("src/app/static/ui/favicon.ico")
    assert favicon_path.is_file()
    assert favicon_path.stat().st_size > 0
