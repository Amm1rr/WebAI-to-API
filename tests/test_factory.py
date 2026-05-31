import pytest
from app.services.factory import ProviderFactory
from app.schemas.request import OpenAIChatRequest
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.atlas import AtlasProvider

def test_get_provider_default():
    """Verify ProviderFactory defaults to Gemini when no explicit provider is specified."""
    request = OpenAIChatRequest(messages=[], model="some-model")
    provider, model = ProviderFactory.get_provider(request)
    
    assert isinstance(provider, GeminiProvider)
    assert model == "some-model"

def test_get_provider_explicit_gemini():
    """Verify ProviderFactory returns Gemini when explicitly requested via provider field."""
    request = OpenAIChatRequest(messages=[], model="some-model", provider="gemini")
    provider, model = ProviderFactory.get_provider(request)
    
    assert isinstance(provider, GeminiProvider)
    assert model == "some-model"

def test_get_provider_explicit_atlas():
    """Verify ProviderFactory returns Atlas when explicitly requested via provider field."""
    request = OpenAIChatRequest(messages=[], model="some-model", provider="atlas")
    provider, model = ProviderFactory.get_provider(request)
    
    assert isinstance(provider, AtlasProvider)
    assert model == "some-model"

def test_get_provider_model_prefix_atlas():
    """Verify ProviderFactory returns Atlas when model name has 'atlas/' prefix."""
    request = OpenAIChatRequest(messages=[], model="atlas/MiniMax-M2")
    provider, model = ProviderFactory.get_provider(request)
    
    assert isinstance(provider, AtlasProvider)
    assert model == "MiniMax-M2"

def test_get_provider_model_prefix_gemini():
    """Verify ProviderFactory returns Gemini when model name has 'gemini/' prefix."""
    request = OpenAIChatRequest(messages=[], model="gemini/gemini-3-flash")
    provider, model = ProviderFactory.get_provider(request)
    
    assert isinstance(provider, GeminiProvider)
    assert model == "gemini-3-flash"

def test_get_provider_unknown_prefix_defaults_to_gemini():
    """Verify ProviderFactory defaults to Gemini for unknown model prefixes."""
    request = OpenAIChatRequest(messages=[], model="unknown/model")
    provider, model = ProviderFactory.get_provider(request)
    
    assert isinstance(provider, GeminiProvider)
    assert model == "unknown/model"

@pytest.mark.asyncio
async def test_close_provider(mocker):
    """Verify close_provider pop and close only the targeted provider instance."""
    mock_gemini = mocker.AsyncMock()
    mock_atlas = mocker.AsyncMock()
    
    # Setup instances
    ProviderFactory._instances = {
        "gemini": mock_gemini,
        "atlas": mock_atlas,
    }

    await ProviderFactory.close_provider("gemini")

    assert "gemini" not in ProviderFactory._instances
    assert "atlas" in ProviderFactory._instances
    mock_gemini.close.assert_called_once()
    mock_atlas.close.assert_not_called()
