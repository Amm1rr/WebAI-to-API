import pytest
from app.services.factory import ProviderFactory
from app.schemas.request import OpenAIChatRequest
from app.services.providers.gemini import GeminiProvider
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
