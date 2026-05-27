import pytest
from app.services.factory import ProviderFactory

@pytest.fixture(autouse=True)
def reset_provider_factory():
    """Reset ProviderFactory shared state before and after each test."""
    ProviderFactory._instances = {}
    yield
    ProviderFactory._instances = {}
