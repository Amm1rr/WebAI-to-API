import pytest
from unittest.mock import AsyncMock
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
    """Verify close_provider pops, closes, and allows a fresh provider instance later."""
    mock_gemini = mocker.AsyncMock()
    mock_atlas = mocker.AsyncMock()
    new_gemini = mocker.Mock()

    mocker.patch.dict(ProviderFactory._registry, {"gemini": lambda: new_gemini})
    
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

    request = OpenAIChatRequest(messages=[], provider="gemini")
    provider, _ = ProviderFactory.get_provider(request)

    assert provider is new_gemini
    assert ProviderFactory._instances["gemini"] is new_gemini


@pytest.mark.asyncio
async def test_close_provider_does_not_shutdown_browser_runtime(mocker):
    """ProviderFactory close_provider is cache invalidation, not browser shutdown."""
    mock_gemini = mocker.AsyncMock()
    ProviderFactory._instances = {"gemini": mock_gemini}

    browser_close = mocker.patch(
        "app.services.browser.engine.BrowserEngine.close",
        new_callable=AsyncMock,
    )
    session_close_resources = mocker.patch(
        "app.services.browser.session.ProviderSession.close_resources",
        new_callable=AsyncMock,
    )

    await ProviderFactory.close_provider("gemini")

    mock_gemini.close.assert_awaited_once()
    browser_close.assert_not_called()
    session_close_resources.assert_not_called()


@pytest.mark.asyncio
async def test_close_all_closes_cached_providers_and_clears_cache(mocker):
    """Verify close_all closes all cached providers without implying app shutdown authority."""
    mock_gemini = mocker.AsyncMock()
    mock_atlas = mocker.AsyncMock()
    ProviderFactory._instances = {
        "gemini": mock_gemini,
        "atlas": mock_atlas,
    }

    await ProviderFactory.close_all()

    mock_gemini.close.assert_awaited_once()
    mock_atlas.close.assert_awaited_once()
    assert ProviderFactory._instances == {}


@pytest.mark.asyncio
async def test_lifespan_shutdown_uses_browser_engine_not_provider_factory(mocker):
    """Application shutdown closes BrowserEngine and does not call ProviderFactory.close_all."""
    from app.main import app, lifespan

    mock_auth_manager = mocker.Mock()
    mock_auth_manager.set_strategy = mocker.Mock()
    mock_auth_manager.refresh_status = mocker.Mock()
    mock_engine = mocker.Mock()
    mock_engine.close = mocker.AsyncMock()

    mocker.patch("app.main.init_gemini_client", new_callable=AsyncMock, return_value=True)
    mocker.patch("app.main.init_session_managers", new_callable=AsyncMock)
    mocker.patch("app.main.get_auth_manager", return_value=mock_auth_manager)
    mocker.patch(
        "app.services.browser.engine.get_browser_engine",
        new_callable=AsyncMock,
        return_value=mock_engine,
    )
    close_all = mocker.patch.object(ProviderFactory, "close_all", new_callable=AsyncMock)

    async with lifespan(app):
        pass

    mock_engine.close.assert_awaited_once()
    close_all.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_auth_recovery_invalidates_provider_cache(mocker):
    """Gemini post-login recovery uses close_provider before reinitializing Gemini services."""
    from app.services.providers.gemini.auth import GeminiAuthStrategy

    close_provider = mocker.patch(
        "app.services.factory.ProviderFactory.close_provider",
        new_callable=AsyncMock,
    )
    init_client = mocker.patch(
        "app.services.providers.gemini.client.init_gemini_client",
        new_callable=AsyncMock,
        return_value=True,
    )
    init_managers = mocker.patch(
        "app.services.providers.gemini.session_manager.init_session_managers",
        new_callable=AsyncMock,
    )

    await GeminiAuthStrategy().run_post_login_recovery()

    close_provider.assert_awaited_once_with("gemini")
    init_client.assert_awaited_once()
    init_managers.assert_awaited_once()
