from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from app.services.factory import ProviderFactory

@pytest.fixture(autouse=True)
def reset_provider_factory():
    """Reset ProviderFactory and AuthManager shared state before and after each test."""
    ProviderFactory._instances = {}
    try:
        from app.services.browser.auth_manager import get_auth_manager
        auth_mgr = get_auth_manager()
        auth_mgr._cached_playwright_status = None
        auth_mgr._cached_webapi_status = None
        if hasattr(auth_mgr, 'coordination_lock'):
            auth_mgr.coordination_lock.release()
    except Exception:
        pass
    yield
    ProviderFactory._instances = {}
    try:
        from app.services.browser.auth_manager import get_auth_manager
        auth_mgr = get_auth_manager()
        auth_mgr._cached_playwright_status = None
        auth_mgr._cached_webapi_status = None
        if hasattr(auth_mgr, 'coordination_lock'):
            auth_mgr.coordination_lock.release()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def reset_gemini_lifecycle_state():
    """Keep generation lease state isolated between tests."""
    try:
        import app.services.providers.gemini.client as gemini_client
        gemini_client._gemini_client = None
        gemini_client._initialization_error = None
        gemini_client._gemini_client_auth_source = None
        gemini_client._gemini_generation_records.clear()
        gemini_client._gemini_client_generations.clear()
        gemini_client._current_gemini_generation = None
        gemini_client._gemini_shutdown_started = False
    except Exception:
        pass

    yield
    try:
        gemini_client._gemini_client = None
        gemini_client._gemini_generation_records.clear()
        gemini_client._gemini_client_generations.clear()
        gemini_client._current_gemini_generation = None
        gemini_client._gemini_shutdown_started = False
    except Exception:
        pass


@pytest.fixture
def install_gemini_client():
    """Install mock client as current registered Gemini lifecycle generation."""
    def install(client, generation=0, auth_source=None):
        import app.services.providers.gemini.client as gemini_client

        if gemini_client._gemini_generation_records:
            raise AssertionError("Test Gemini lifecycle state is already installed.")
        gemini_client.register_gemini_generation(client, generation=generation)
        gemini_client._gemini_client = client
        gemini_client._current_gemini_generation = generation
        gemini_client._gemini_shutdown_started = False
        gemini_client._initialization_error = None
        gemini_client._gemini_client_auth_source = auth_source
        return generation

    return install
