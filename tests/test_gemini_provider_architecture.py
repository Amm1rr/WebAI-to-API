import pytest
from fastapi import HTTPException
from app.config import load_config
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.gemini.shared import get_gemini_models
from app.schemas.request import OpenAIChatRequest

def test_invalid_gemini_backend_config_fails_fast(tmp_path):
    """Verify that an invalid Gemini backend configuration raises ValueError."""
    config_file = tmp_path / "invalid_config.conf"
    config_file.write_text("[Gemini]\nbackend = invalid_strategy\n")
    
    with pytest.raises(ValueError) as excinfo:
        load_config(str(config_file))
    
    assert "Invalid Gemini backend configured" in str(excinfo.value)
    assert "invalid_strategy" in str(excinfo.value)

@pytest.mark.asyncio
async def test_list_models_stability_across_backends(mocker):
    """Verify that list_models returns the same list regardless of the configured backend."""
    # 1. Test with webapi backend
    mocker.patch("app.services.providers.gemini.provider.CONFIG", {"Gemini": {"backend": "webapi"}})
    provider_webapi = GeminiProvider()
    models_webapi = await provider_webapi.list_models()
    
    # 2. Test with playwright backend
    mocker.patch("app.services.providers.gemini.provider.CONFIG", {"Gemini": {"backend": "playwright"}})
    provider_playwright = GeminiProvider()
    models_playwright = await provider_playwright.list_models()
    
    # 3. Verify stability
    assert models_webapi == models_playwright
    assert len(models_webapi) > 0
    assert any("gemini-3" in m["id"] for m in models_webapi)
    # Ensure it's not just the single "gemini" model from Playwright
    assert len(models_webapi) > 1

def test_import_graph_safety():
    """Verify that components can be imported without relying on package-level side effects."""
    # These should work independently
    from app.services.providers.gemini.shared import get_gemini_models
    from app.services.providers.gemini.persistence import serialize_session_state
    from app.services.providers.gemini.provider import GeminiProvider
    from app.utils.tokens import generate_opaque_token
    
    assert get_gemini_models is not None
    assert serialize_session_state is not None
    assert GeminiProvider is not None
    assert generate_opaque_token is not None

@pytest.mark.asyncio
async def test_gemini_provider_adapter_selection_logic(mocker):
    """Verify that GeminiProvider selects the correct adapter based on config and model prefix."""
    from app.services.providers.gemini.webapi_adapter import GeminiWebAPIAdapter
    from app.services.providers.gemini.playwright_adapter import GeminiPlaywrightAdapter

    # Case 1: Default webapi, standard model
    mocker.patch("app.services.providers.gemini.provider.CONFIG", {"Gemini": {"backend": "webapi"}})
    provider = GeminiProvider()
    adapter = provider._get_adapter("gemini-3-flash")
    assert isinstance(adapter, GeminiWebAPIAdapter)
    
    # Case 2: Default webapi, playwright/ prefix
    adapter_prefix = provider._get_adapter("playwright/gemini-3-flash")
    assert isinstance(adapter_prefix, GeminiPlaywrightAdapter)
    
    # Case 3: Default playwright, standard model
    mocker.patch("app.services.providers.gemini.provider.CONFIG", {"Gemini": {"backend": "playwright"}})
    provider_pw = GeminiProvider()
    adapter_pw = provider_pw._get_adapter("gemini-3-flash")
    assert isinstance(adapter_pw, GeminiPlaywrightAdapter)
