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
        auth_mgr.login_state = "IDLE"
    except Exception:
        pass
    yield
    ProviderFactory._instances = {}
    try:
        from app.services.browser.auth_manager import get_auth_manager
        auth_mgr = get_auth_manager()
        auth_mgr._cached_playwright_status = None
        auth_mgr._cached_webapi_status = None
        auth_mgr.login_state = "IDLE"
    except Exception:
        pass
