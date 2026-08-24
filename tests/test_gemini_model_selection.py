import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from app.services.providers.gemini.browser_adapter import GeminiProviderAdapter
from app.services.providers.gemini.playwright_adapter import (
    GeminiPlaywrightAdapter,
    BrowserRequestState,
    BrowserRequestConfig
)
from app.services.providers.gemini.shared import PLAYWRIGHT_GEMINI_MODEL_UI_LABELS, get_gemini_models
from app.services.providers.gemini.scripts.gemini_scripts import SELECTORS
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
        from app.services.providers.gemini.scripts.gemini_scripts import SELECTORS
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
    opt_flash.get_attribute = AsyncMock(return_value=None)

    opt_pro = MagicMock()
    opt_pro.inner_text = AsyncMock(return_value="3.1 Pro")
    opt_pro.get_attribute = AsyncMock(return_value=None)
    opt_pro.click = AsyncMock()
    
    mock_options.nth.side_effect = [opt_flash, opt_pro]
    
    # Route locator calls
    def locator_side_effect(selector):
        from app.services.providers.gemini.scripts.gemini_scripts import SELECTORS
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
        from app.services.providers.gemini.scripts.gemini_scripts import SELECTORS
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
    opt_lite.get_attribute = AsyncMock(return_value=None)
    opt_lite.click = AsyncMock()

    opt_flash = MagicMock()
    opt_flash.inner_text = AsyncMock(return_value="Gemini 1.5 Flash")
    opt_flash.get_attribute = AsyncMock(return_value=None)
    opt_flash.click = AsyncMock()
    
    mock_options.nth.side_effect = [opt_lite, opt_flash, opt_lite, opt_flash]
    
    # Route locator calls
    def locator_side_effect(selector):
        from app.services.providers.gemini.scripts.gemini_scripts import SELECTORS
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


GEM_MENU_SEL = '[role="menu"][data-test-id="gem-mode-menu"]'


def _build_mode_selection_page(options, id_item_classes=None, id_match_count=1, events=None):
    """
    Build a mock page for select_model flows.

    options: list of {"text": str, "mode_id": Optional[str]}
    id_item_classes: side_effect (or value) for the matched mode-id item's
                     get_attribute("class"); None -> never queried
    id_match_count: how many items the post-click data-mode-id query returns
    events: optional list; interaction lifecycle is appended to it as
            "picker_click", "menu_open", "escape"

    Returns (page, selectors_seen, option_els).
    """
    page = MagicMock()
    page._gemini_callbacks = {}
    page.url = "https://gemini.google.com/app"
    selectors_seen = []
    option_els = []
    record = events.append if events is not None else (lambda _e: None)

    def make_loc():
        loc = MagicMock()
        loc.first = loc
        return loc

    picker_el = MagicMock()
    picker_el.count = AsyncMock(return_value=1)
    picker_el.get_attribute = AsyncMock(return_value=None)
    picker_el.inner_text = AsyncMock(return_value="")

    async def _picker_click(*_a, **_k):
        record("picker_click")

    picker_el.click = _picker_click
    picker_el.scroll_into_view_if_needed = AsyncMock()

    menu_loc = make_loc()

    async def _menu_wait(*_a, **_k):
        record("menu_open")

    menu_loc.first.wait_for = _menu_wait

    # Reuse one id-locator instance per selector so sequential class reads
    # (one per verification poll) advance the same side_effect sequence.
    id_locators = {}

    def make_id_locator():
        loc = MagicMock()
        loc.count = AsyncMock(return_value=id_match_count)
        item = MagicMock()
        if isinstance(id_item_classes, list):
            item.get_attribute = AsyncMock(side_effect=list(id_item_classes))
        else:
            item.get_attribute = AsyncMock(return_value=id_item_classes)
        loc.nth.return_value = item
        return loc

    # Build option elements once: every MODEL_OPTION locator call must return
    # the SAME instances so tests observe the clicks production performs.
    for opt in options:
        el = MagicMock()
        el.inner_text = AsyncMock(return_value=opt["text"])
        el.get_attribute = AsyncMock(return_value=opt["mode_id"])
        el.click = AsyncMock()
        option_els.append(el)

    def make_opts():
        opts = MagicMock()
        opts.first = opts
        opts.wait_for = AsyncMock()
        opts.count = AsyncMock(return_value=len(options))
        for i, el in enumerate(option_els):
            setattr(opts, f"opt_{i}", el)

        def _nth(i, _opts=opts):
            return getattr(_opts, f"opt_{i}")

        opts.nth = _nth
        return opts

    def locator_side_effect(selector):
        selectors_seen.append(selector)
        if selector == SELECTORS["MODEL_PICKER"]:
            loc = make_loc()
            loc.first = picker_el
            return loc
        if selector == GEM_MENU_SEL:
            return menu_loc
        if selector.startswith('gem-menu-item[data-mode-id='):
            if selector not in id_locators:
                id_locators[selector] = make_id_locator()
            return id_locators[selector]
        if selector == SELECTORS["MODEL_OPTION"]:
            return make_opts()
        return make_loc()

    page.locator.side_effect = locator_side_effect

    async def _escape(key, **_k):
        record("escape")

    page.keyboard.press = _escape
    return page, selectors_seen, option_els


@pytest.mark.asyncio
async def test_select_model_verifies_via_captured_mode_id_without_picker_text(adapter, monkeypatch):
    options = [{"text": "3.6 Flash", "mode_id": "abc123session"}]
    page, seen, opts = _build_mode_selection_page(
        options, id_item_classes="ng-star-inserted selected"
    )
    monkeypatch.setattr(
        "app.services.providers.gemini.browser_adapter.asyncio.sleep", AsyncMock()
    )

    await adapter.select_model(page, "Flash")

    assert opts[0].click.call_count == 1
    assert 'gem-menu-item[data-mode-id="abc123session"]' in seen
    # Picker text stayed unusable throughout; verification still succeeded.
    assert GEM_MENU_SEL in seen


@pytest.mark.asyncio
async def test_select_model_without_mode_id_uses_text_verification_fallback(adapter):
    options = [{"text": "3.1 Pro", "mode_id": None}]
    page, seen, opts = _build_mode_selection_page(options)
    # No-op pre-check sees a different model; post-click check sees the target.
    active_reads = iter(["Flash", "Pro", "Pro", "Pro", "Pro", "Pro"])

    async def fake_get_active_model(_page):
        return next(active_reads, "Pro")

    adapter.get_active_model = fake_get_active_model

    await adapter.select_model(page, "Pro")

    assert opts[0].click.call_count == 1
    assert not any("data-mode-id" in s for s in seen)


@pytest.mark.asyncio
async def test_select_model_mode_id_never_selected_fails_transient(adapter, monkeypatch):
    options = [{"text": "3.6 Flash", "mode_id": "abc123session"}]
    page, _, _ = _build_mode_selection_page(
        options, id_item_classes=["ng-star-inserted"] * 10
    )
    monkeypatch.setattr(
        "app.services.providers.gemini.browser_adapter.asyncio.sleep", AsyncMock()
    )

    with pytest.raises(TransientSessionError, match="never became selected"):
        await adapter.select_model(page, "Flash")


@pytest.mark.asyncio
async def test_select_model_duplicate_mode_id_is_ambiguous_failure(adapter, monkeypatch):
    options = [{"text": "3.6 Flash", "mode_id": "abc123session"}]
    page, _, _ = _build_mode_selection_page(
        options,
        id_item_classes="ng-star-inserted selected",
        id_match_count=2,
    )

    with pytest.raises(TransientSessionError, match="ambiguous"):
        await adapter.select_model(page, "Flash")


@pytest.mark.asyncio
async def test_select_model_data_active_without_selected_does_not_count(adapter, monkeypatch):
    options = [{"text": "3.6 Flash", "mode_id": "abc123session"}]
    page, _, _ = _build_mode_selection_page(
        options, id_item_classes=["active"] * 10
    )
    monkeypatch.setattr(
        "app.services.providers.gemini.browser_adapter.asyncio.sleep", AsyncMock()
    )

    with pytest.raises(TransientSessionError, match="never became selected"):
        await adapter.select_model(page, "Flash")


@pytest.mark.asyncio
async def test_mode_id_unsuccessful_poll_escapes_before_retry_reopens_cleanly(adapter, monkeypatch):
    events = []
    options = [{"text": "3.6 Flash", "mode_id": "abc123session"}]
    # Poll 1: not selected -> Escape + backoff; poll 2: selected -> Escape + success.
    page, _, opts = _build_mode_selection_page(
        options,
        id_item_classes=["ng-star-inserted", "ng-star-inserted selected"],
        events=events,
    )

    async def _sleep(_delay):
        events.append("sleep")

    monkeypatch.setattr(
        "app.services.providers.gemini.browser_adapter.asyncio.sleep", _sleep
    )

    await adapter.select_model(page, "Flash")

    assert opts[0].click.call_count == 1
    assert events == [
        "picker_click",   # select_model opens options menu
        "picker_click",   # verification poll 1 reopens menu
        "menu_open",
        "escape",         # not selected: close before backoff
        "sleep",
        "picker_click",   # poll 2 reopens cleanly (no toggle-shut)
        "menu_open",
        "escape",         # success closes menu
    ]


@pytest.mark.asyncio
async def test_mode_id_timeout_leaves_menu_closed(adapter, monkeypatch):
    events = []
    options = [{"text": "3.6 Flash", "mode_id": "abc123session"}]
    page, _, _ = _build_mode_selection_page(
        options, id_item_classes="ng-star-inserted", events=events
    )
    monkeypatch.setattr(
        "app.services.providers.gemini.browser_adapter.asyncio.sleep", AsyncMock()
    )

    with pytest.raises(TransientSessionError, match="never became selected"):
        await adapter.select_model(page, "Flash")

    assert events[-1] == "escape"
    assert events.count("picker_click") == 7  # initial open + 6 polls


@pytest.mark.asyncio
async def test_mode_id_ambiguous_match_closes_menu_before_error(adapter):
    events = []
    options = [{"text": "3.6 Flash", "mode_id": "abc123session"}]
    page, _, _ = _build_mode_selection_page(
        options,
        id_item_classes="ng-star-inserted selected",
        id_match_count=2,
        events=events,
    )

    with pytest.raises(TransientSessionError, match="ambiguous"):
        await adapter.select_model(page, "Flash")

    assert events == ["picker_click", "picker_click", "menu_open", "escape"]

def test_get_gemini_models_preserves_playwright_models_without_runtime_catalog():
    """Runtime catalog absence must not remove Playwright models."""
    models = get_gemini_models(None)
    model_ids = [m["id"] for m in models]
    
    # 1. Verify exactly these models exist
    verified_playwright_models = [
        "playwright/gemini-3.1-pro",
        "playwright/gemini-3.5-flash",
        "playwright/gemini-3.1-flash-lite"
    ]
    for model_id in verified_playwright_models:
        assert model_id in model_ids, f"Expected {model_id} to be advertised"

    canonical_playwright_models = [
        "playwright/gemini/gemini-3.1-pro",
        "playwright/gemini/gemini-3.5-flash",
        "playwright/gemini/gemini-3.1-flash-lite",
    ]
    for model_id in canonical_playwright_models:
        assert model_id in model_ids, f"Expected canonical browser namespace model {model_id} to be advertised"
        
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
    mock_state = MagicMock(spec=BrowserRequestState)
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
async def test_orchestrate_model_selection_browser_namespace_aliases():
    """Verify provider-aware browser namespaces normalize to the same Gemini UI labels."""
    adapter = GeminiPlaywrightAdapter(MagicMock())
    mock_browser_adapter = MagicMock(spec=GeminiProviderAdapter)
    mock_browser_adapter.select_model = AsyncMock()
    mock_page = MagicMock()
    mock_state = MagicMock(spec=BrowserRequestState)
    mock_state.active_tab = MagicMock()

    await adapter._orchestrate_model_selection(
        mock_browser_adapter, mock_page, "playwright/gemini/gemini-3.5-flash", mock_state
    )
    await adapter._orchestrate_model_selection(
        mock_browser_adapter, mock_page, "playwright/gemini/gemini-3.1-pro", mock_state
    )

    assert mock_browser_adapter.select_model.call_count == 2
    mock_browser_adapter.select_model.assert_any_call(mock_page, "Flash", mock_state)
    mock_browser_adapter.select_model.assert_any_call(mock_page, "Pro", mock_state)

@pytest.mark.asyncio
async def test_orchestrate_model_selection_unsupported_aliases_fail():
    """Verify that unverified legacy aliases fail fast with HTTP 400."""
    adapter = GeminiPlaywrightAdapter(MagicMock())
    mock_browser_adapter = MagicMock(spec=GeminiProviderAdapter)
    mock_page = MagicMock()
    mock_state = MagicMock(spec=BrowserRequestState)
    
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
    mock_state = MagicMock(spec=BrowserRequestState)
    
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
    mock_state = MagicMock(spec=BrowserRequestState)
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
    mock_state = MagicMock(spec=BrowserRequestState)
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
    mock_state = MagicMock(spec=BrowserRequestState)
    mock_state.active_tab = MagicMock()
    
    await adapter._orchestrate_model_selection(
        mock_browser_adapter, 
        mock_page, 
        "playwright/gemini-3.5-flash", 
        mock_state
    )
    
    mock_browser_adapter.select_model.assert_called_once_with(mock_page, "Flash", mock_state)
    mock_state.active_tab.heartbeat.assert_called_with("model_selection")
