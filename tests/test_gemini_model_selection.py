import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from app.services.browser.adapters.gemini_adapter import GeminiProviderAdapter
from app.services.providers.gemini.playwright_adapter import (
    GeminiPlaywrightAdapter, 
    PlaywrightRequestState, 
    PlaywrightAdapterConfig
)
from app.services.providers.gemini.shared import PLAYWRIGHT_GEMINI_MODEL_UI_LABELS
from app.schemas.request import OpenAIChatRequest
from app.services.browser.errors import TransientSessionError, GatedModelError, ModelNotFoundError
from app.services.browser.auth_types import AuthStatus

@pytest.fixture
def mock_page():
    page = MagicMock()
    page._gemini_callbacks = {}
    page.locator = MagicMock()
    # Ensure all common awaited methods are AsyncMock
    page.click = AsyncMock()
    page.inner_text = AsyncMock()
    page.get_attribute = AsyncMock()
    page.content = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="Gemini")
    page.query_selector_all = AsyncMock(return_value=[])
    
    async def fake_evaluate(*args, **kwargs):
        return None
    page.evaluate = AsyncMock(side_effect=fake_evaluate)
    
    page.on = MagicMock()
    page.url = "https://gemini.google.com/app"
    return page

@pytest.fixture
def adapter():
    return GeminiProviderAdapter()

def mock_locator_element():
    el = MagicMock()
    el.count = AsyncMock(return_value=1)
    el.get_attribute = AsyncMock(return_value=None)
    el.inner_text = AsyncMock(return_value="")
    el.click = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    return el

@pytest.mark.asyncio
async def test_get_active_model_via_aria_label(adapter, mock_page):
    mock_picker = mock_locator_element()
    mock_picker.get_attribute = AsyncMock(return_value="Open mode picker, currently Flash")
    
    mock_loc = MagicMock()
    mock_loc.first = mock_picker
    mock_page.locator.return_value = mock_loc
    
    model = await adapter.get_active_model(mock_page)
    assert model == "Flash"

@pytest.mark.asyncio
async def test_get_active_model_via_text_fallback(adapter, mock_page):
    mock_picker = mock_locator_element()
    mock_picker.get_attribute = AsyncMock(return_value=None)
    mock_picker.inner_text = AsyncMock(return_value="Pro")
    
    mock_loc = MagicMock()
    mock_loc.first = mock_picker
    mock_page.locator.return_value = mock_loc
    
    model = await adapter.get_active_model(mock_page)
    assert model == "Pro"

@pytest.mark.asyncio
async def test_find_model_picker_fallbacks(adapter, mock_page):
    # Setup: Primary fails, first fallback succeeds
    mock_primary = MagicMock()
    mock_primary.count = AsyncMock(return_value=0)
    mock_primary.first = MagicMock()
    mock_primary.first.count = AsyncMock(return_value=0)
    
    mock_fallback_el = mock_locator_element()
    mock_fallback_loc = MagicMock()
    mock_fallback_loc.count = AsyncMock(return_value=1)
    mock_fallback_loc.first = mock_fallback_el
    
    def locator_side_effect(selector):
        from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
        if selector == SELECTORS["MODEL_PICKER"]:
            return mock_primary
        if selector == 'button[aria-label*="Select model"]':
            return mock_fallback_loc
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    
    picker = await adapter._find_model_picker(mock_page)
    assert picker == mock_fallback_el

@pytest.mark.asyncio
async def test_select_model_no_op_when_already_active(adapter, mock_page):
    # Setup: Active model is 'Flash'
    mock_picker = mock_locator_element()
    mock_picker.get_attribute = AsyncMock(return_value="Open mode picker, currently Flash")
    
    mock_loc = MagicMock()
    mock_loc.first = mock_picker
    mock_page.locator.return_value = mock_loc
    
    # Requesting 'Flash' should no-op
    await adapter.select_model(mock_page, "Flash")
    
    # Verify picker was not clicked
    assert mock_picker.click.call_count == 0

@pytest.mark.asyncio
async def test_select_model_success(adapter, mock_page):
    # Initial model 'Flash', then 'Pro'
    mock_picker = mock_locator_element()
    mock_picker.get_attribute = AsyncMock(side_effect=["Open mode picker, currently Flash", "Open mode picker, currently Pro"])
    
    mock_picker_loc = MagicMock()
    mock_picker_loc.first = mock_picker
    
    # Options menu
    mock_options = MagicMock()
    mock_options.count = AsyncMock(return_value=2)
    mock_options.first.wait_for = AsyncMock()
    
    opt_flash = MagicMock()
    opt_flash.inner_text = AsyncMock(return_value="3.5 Flash")
    
    opt_pro = MagicMock()
    opt_pro.inner_text = AsyncMock(return_value="3.1 Pro")
    opt_pro.click = AsyncMock()
    
    mock_options.nth.side_effect = [opt_flash, opt_pro]
    
    # Route locator calls
    def locator_side_effect(selector):
        from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
        if selector == SELECTORS["MODEL_PICKER"]:
            return mock_picker_loc
        if selector == SELECTORS["MODEL_OPTION"]:
            return mock_options
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    
    await adapter.select_model(mock_page, "Pro")
    
    # Verify picker was clicked and target option was clicked
    assert mock_picker.click.call_count == 1
    assert opt_pro.click.call_count == 1

@pytest.mark.asyncio
async def test_select_model_diagnostics_on_failure(adapter, mock_page):
    # Setup: No picker found
    mock_loc = MagicMock()
    mock_loc.count = AsyncMock(return_value=0)
    mock_loc.first = MagicMock()
    mock_loc.first.count = AsyncMock(return_value=0)
    mock_page.locator.return_value = mock_loc
    
    mock_page.title = AsyncMock(return_value="Gemini App Title")
    mock_page.url = "https://gemini.google.com/app/123"
    
    mock_btn = MagicMock()
    mock_btn.get_attribute = AsyncMock(return_value="Help")
    mock_page.query_selector_all.return_value = [mock_btn]
    
    with pytest.raises(TransientSessionError) as excinfo:
        await adapter.select_model(mock_page, "Pro")
    
    assert "Gemini model picker not found" in str(excinfo.value)
    assert "Gemini App Title" in str(excinfo.value)
    assert "https://gemini.google.com/app/123" in str(excinfo.value)
    assert "['Help']" in str(excinfo.value)

@pytest.mark.asyncio
async def test_select_model_fails_when_gated_advanced(adapter, mock_page):
    # Initial model 'Flash'
    mock_picker = mock_locator_element()
    mock_picker.get_attribute = AsyncMock(return_value="Open mode picker, currently Flash")
    
    mock_picker_loc = MagicMock()
    mock_picker_loc.first = mock_picker
    
    mock_options = MagicMock()
    mock_options.count = AsyncMock(return_value=1)
    mock_options.first.wait_for = AsyncMock()
    opt_flash = MagicMock()
    opt_flash.inner_text = AsyncMock(return_value="3.5 Flash")
    mock_options.nth.return_value = opt_flash
    
    # Route locator calls
    def locator_side_effect(selector):
        from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
        if selector == SELECTORS["MODEL_PICKER"]:
            return mock_picker_loc
        if selector == SELECTORS["MODEL_OPTION"]:
            return mock_options
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    mock_page.content.return_value = "... Try Gemini Advanced ..."
    
    # VERIFY: Should raise GatedModelError, NOT HTTPException
    with pytest.raises(GatedModelError) as excinfo:
        await adapter.select_model(mock_page, "Pro")
    
    assert "gated behind a Gemini Advanced subscription" in str(excinfo.value)

@pytest.mark.asyncio
async def test_model_mapping_constant_validity():
    """Verify that the mapping constant only contains verified labels."""
    supported_labels = ["Pro", "Flash", "Flash-Lite"]
    for label in PLAYWRIGHT_GEMINI_MODEL_UI_LABELS.values():
        assert label in supported_labels

@pytest.mark.asyncio
async def test_select_model_collision_prevention_flash_vs_lite(adapter, mock_page):
    """Verify that requesting 'Flash' doesn't accidentally pick 'Flash-Lite'."""
    # Setup: 'Flash-Lite' is the first option, '3.5 Flash' is the second
    mock_picker = mock_locator_element()
    mock_picker.get_attribute.side_effect = ["Open mode picker, currently Pro", "Open mode picker, currently Gemini 1.5 Flash"]
    
    mock_picker_loc = MagicMock()
    mock_picker_loc.first = mock_picker
    
    mock_options = MagicMock()
    mock_options.count = AsyncMock(return_value=2)
    mock_options.first.wait_for = AsyncMock()
    
    opt_lite = MagicMock()
    opt_lite.inner_text = AsyncMock(return_value="Gemini 1.5 Flash-Lite")
    opt_lite.click = AsyncMock()
    
    opt_flash = MagicMock()
    opt_flash.inner_text = AsyncMock(return_value="Gemini 1.5 Flash")
    opt_flash.click = AsyncMock()
    
    mock_options.nth.side_effect = [opt_lite, opt_flash, opt_lite, opt_flash]
    
    # Route locator calls
    def locator_side_effect(selector):
        from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
        if selector == SELECTORS["MODEL_PICKER"]:
            return mock_picker_loc
        if selector == SELECTORS["MODEL_OPTION"]:
            return mock_options
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    
    # Requesting 'Flash'
    await adapter.select_model(mock_page, "Flash")
    
    # Verify '3.5 Flash' (opt_flash) was clicked, NOT 'Flash-Lite'
    assert opt_flash.click.call_count == 1
    assert opt_lite.click.call_count == 0

@pytest.mark.asyncio
async def test_get_gemini_models_discovery_consistency():
    """Verify that get_gemini_models returns exactly the verified playwright models."""
    from app.services.providers.gemini.shared import get_gemini_models
    
    models = get_gemini_models()
    model_ids = [m["id"] for m in models]
    
    # 1. Verify exactly these models exist
    verified_playwright_models = [
        "playwright/gemini-3.1-pro",
        "playwright/gemini-3.5-flash",
        "playwright/gemini-3.1-flash-lite"
    ]
    for model_id in verified_playwright_models:
        assert model_id in model_ids, f"Expected {model_id} to be advertised"
        
    # 2. Verify specific unverified aliases are NOT advertised
    unverified = [
        "playwright/gemini-3-pro",
        "playwright/gemini-3-thinking",
        "playwright/gemini-1.5-pro"
    ]
    for model_id in unverified:
        assert model_id not in model_ids, f"Did NOT expect {model_id} to be advertised"

# --- Orchestration Unit Tests ---

@pytest.mark.asyncio
async def test_orchestrate_model_selection_versioned_aliases():
    """Verify ONLY restricted versioned aliases map correctly."""
    adapter = GeminiPlaywrightAdapter(MagicMock())
    mock_browser_adapter = MagicMock(spec=GeminiProviderAdapter)
    mock_browser_adapter.select_model = AsyncMock()
    mock_page = MagicMock()
    mock_state = MagicMock(spec=PlaywrightRequestState)
    mock_state.active_tab = MagicMock()
    
    # Verified Models
    await adapter._orchestrate_model_selection(
        mock_browser_adapter, mock_page, "playwright/gemini-3.5-flash", mock_state
    )
    await adapter._orchestrate_model_selection(
        mock_browser_adapter, mock_page, "playwright/gemini-3.1-pro", mock_state
    )
    await adapter._orchestrate_model_selection(
        mock_browser_adapter, mock_page, "playwright/gemini-3.1-flash-lite", mock_state
    )
    
    assert mock_browser_adapter.select_model.call_count == 3
    mock_browser_adapter.select_model.assert_any_call(mock_page, "Flash", mock_state)
    mock_browser_adapter.select_model.assert_any_call(mock_page, "Pro", mock_state)
    mock_browser_adapter.select_model.assert_any_call(mock_page, "Flash-Lite", mock_state)

@pytest.mark.asyncio
async def test_orchestrate_model_selection_unsupported_aliases_fail():
    """Verify that unverified legacy aliases fail fast with HTTP 400."""
    adapter = GeminiPlaywrightAdapter(MagicMock())
    mock_browser_adapter = MagicMock(spec=GeminiProviderAdapter)
    mock_page = MagicMock()
    mock_state = MagicMock(spec=PlaywrightRequestState)
    
    unsupported = [
        "playwright/gemini-3-pro",
        "playwright/gemini-1.5-pro",
        "playwright/gemini-3-flash",
        "playwright/gemini-1.5-flash",
        "playwright/gemini-3-flash-lite",
        "playwright/gemini-1.5-flash-lite",
        "playwright/gemini-3-thinking"
    ]
    
    for model in unsupported:
        with pytest.raises(HTTPException) as excinfo:
            await adapter._orchestrate_model_selection(mock_browser_adapter, mock_page, model, mock_state)
        assert excinfo.value.status_code == 400
        assert "no known Playwright UI mapping" in excinfo.value.detail

@pytest.mark.asyncio
async def test_orchestrate_model_selection_unknown_model_fails_400():
    """Verify Task A requirement 1-3: Unknown model fails with HTTP 400 before interaction."""
    adapter = GeminiPlaywrightAdapter(MagicMock())
    mock_browser_adapter = MagicMock(spec=GeminiProviderAdapter)
    mock_page = MagicMock()
    mock_state = MagicMock(spec=PlaywrightRequestState)
    
    with pytest.raises(HTTPException) as excinfo:
        await adapter._orchestrate_model_selection(
            mock_browser_adapter, 
            mock_page, 
            "playwright/unknown-model", 
            mock_state
        )
    
    assert excinfo.value.status_code == 400
    assert "no known Playwright UI mapping" in excinfo.value.detail
    # Verify no interaction occurred
    assert mock_browser_adapter.select_model.call_count == 0

@pytest.mark.asyncio
async def test_orchestrate_model_selection_gated_model_maps_403():
    """Verify Task C requirement: GatedModelError maps to HTTP 403."""
    adapter = GeminiPlaywrightAdapter(MagicMock())
    mock_browser_adapter = MagicMock(spec=GeminiProviderAdapter)
    mock_browser_adapter.select_model = AsyncMock(side_effect=GatedModelError("Paywall"))
    mock_page = MagicMock()
    mock_state = MagicMock(spec=PlaywrightRequestState)
    mock_state.active_tab = MagicMock()
    
    with pytest.raises(HTTPException) as excinfo:
        await adapter._orchestrate_model_selection(
            mock_browser_adapter, 
            mock_page, 
            "playwright/gemini-3.1-pro", 
            mock_state
        )
    
    assert excinfo.value.status_code == 403
    assert "Paywall" in excinfo.value.detail

@pytest.mark.asyncio
async def test_orchestrate_model_selection_not_found_maps_400():
    """Verify Task C requirement: ModelNotFoundError maps to HTTP 400."""
    adapter = GeminiPlaywrightAdapter(MagicMock())
    mock_browser_adapter = MagicMock(spec=GeminiProviderAdapter)
    mock_browser_adapter.select_model = AsyncMock(side_effect=ModelNotFoundError("Not in menu"))
    mock_page = MagicMock()
    mock_state = MagicMock(spec=PlaywrightRequestState)
    mock_state.active_tab = MagicMock()
    
    with pytest.raises(HTTPException) as excinfo:
        await adapter._orchestrate_model_selection(
            mock_browser_adapter, 
            mock_page, 
            "playwright/gemini-3.1-pro", 
            mock_state
        )
    
    assert excinfo.value.status_code == 400
    assert "Not in menu" in excinfo.value.detail

@pytest.mark.asyncio
async def test_orchestrate_model_selection_success():
    """Verify successful orchestration path."""
    adapter = GeminiPlaywrightAdapter(MagicMock())
    mock_browser_adapter = MagicMock(spec=GeminiProviderAdapter)
    mock_browser_adapter.select_model = AsyncMock()
    mock_page = MagicMock()
    mock_state = MagicMock(spec=PlaywrightRequestState)
    mock_state.active_tab = MagicMock()
    
    await adapter._orchestrate_model_selection(
        mock_browser_adapter, 
        mock_page, 
        "playwright/gemini-3.5-flash", 
        mock_state
    )
    
    mock_browser_adapter.select_model.assert_called_once_with(mock_page, "Flash", mock_state)
    mock_state.active_tab.heartbeat.assert_called_with("model_selection")
