import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from app.main import app
from app.services.browser.auth_types import AuthStatus

client = TestClient(app)

@pytest.fixture
def mock_engine_instance():
    with patch("app.endpoints.system.BrowserEngine._instance") as mock:
        engine = MagicMock()
        engine.is_shutting_down = False
        engine.browser = MagicMock()
        engine.browser.is_connected.return_value = True
        engine.browser_generation = 1
        engine.is_bootstrap = False
        engine.sessions = {}
        
        # Configure the patch to return our engine mock
        # Since it's a direct attribute patch, we set it via start() or property
        # But for system.BrowserEngine._instance, we can just point it to engine
        import app.endpoints.system as system
        old_instance = system.BrowserEngine._instance
        system.BrowserEngine._instance = engine
        yield engine
        system.BrowserEngine._instance = old_instance

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
    with patch("app.endpoints.system.BrowserEngine._instance", None):
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
    with patch("app.endpoints.system.BrowserEngine._instance", None):
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
    mock_engine_instance.browser.is_connected.return_value = False
    
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
    with patch("app.endpoints.system.BrowserEngine._instance", None):
        response = client.get("/v1/runtime/status")
        assert response.status_code == 200
        data = response.json()
        assert data["engine"]["status"] == "NOT_INITIALIZED"
