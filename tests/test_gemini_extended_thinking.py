import configparser
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pydantic import ValidationError

from app.main import app
from app.schemas.request import OpenAIChatRequest
from app.services.providers.gemini.browser_adapter import GeminiProviderAdapter
from app.services.browser.errors import GatedModelError, ModelNotFoundError, TransientSessionError
from app.services.providers.atlas.provider import AtlasProvider
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.gemini.temporary_chat import _resolve_temporary_chat_model
from app.services.providers.gemini import playwright_adapter as playwright_module


def make_request(**kwargs):
    return OpenAIChatRequest(
        model=kwargs.pop("model", "playwright/gemini-3.5-flash"),
        messages=[{"role": "user", "content": "hello"}],
        **kwargs,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"provider_options": {"atlas": {"extended_thinking": True}}},
        {"provider_options": {"gemini": {"unknown": True}}},
        {"provider_options": {"gemini": {"extended_thinking": "yes"}}},
    ],
)
def test_provider_options_schema_rejects_invalid_shapes(payload):
    with pytest.raises(ValidationError):
        OpenAIChatRequest.model_validate({**payload, "messages": [{"role": "user", "content": "hello"}]})


def test_provider_options_schema_accepts_extended_thinking_and_defaults_off():
    request = make_request(provider_options={"gemini": {"extended_thinking": True}})
    assert request.provider_options.gemini.extended_thinking is True
    assert make_request().provider_options is None
    assert make_request(provider_options={"gemini": {}}).provider_options.gemini.extended_thinking is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_value", "request_value", "expected"),
    [
        ("false", None, False),
        ("true", None, True),
        ("true", False, False),
        ("false", True, True),
        (None, None, False),
    ],
)
async def test_extended_thinking_config_precedence(monkeypatch, config_value, request_value, expected):
    config = configparser.ConfigParser()
    if config_value is not None:
        config["GeminiPlaywright"] = {"extended_thinking": config_value}
    monkeypatch.setattr(playwright_module, "CONFIG", config)

    provider = GeminiProvider()
    browser_adapter = type("Adapter", (), {"set_extended_thinking": AsyncMock()})()
    provider_options = None if request_value is None else {"gemini": {"extended_thinking": request_value}}
    await provider.playwright_adapter._configure_request_options(
        browser_adapter,
        object(),
        make_request(provider_options=provider_options),
        None,
    )

    assert browser_adapter.set_extended_thinking.await_args.args[1] is expected


@pytest.mark.asyncio
async def test_gemini_playwright_config_does_not_affect_webapi(monkeypatch):
    config = configparser.ConfigParser()
    config["GeminiPlaywright"] = {"extended_thinking": "true"}
    monkeypatch.setattr(playwright_module, "CONFIG", config)

    provider = GeminiProvider()
    provider.webapi_adapter.chat_completions = AsyncMock(return_value={"ok": True})
    result = await provider.chat_completions(make_request(model="gemini-3-flash"))

    assert result == {"ok": True}
    provider.webapi_adapter.chat_completions.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_gemini_playwright_config_key_defaults_off(monkeypatch):
    config = configparser.ConfigParser()
    config["GeminiPlaywright"] = {}
    monkeypatch.setattr(playwright_module, "CONFIG", config)

    provider = GeminiProvider()
    browser_adapter = type("Adapter", (), {"set_extended_thinking": AsyncMock()})()
    await provider.playwright_adapter._configure_request_options(
        browser_adapter,
        object(),
        make_request(),
        None,
    )

    assert browser_adapter.set_extended_thinking.await_args.args[1] is False


@pytest.mark.asyncio
async def test_http_schema_errors_are_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for options in (
            {"atlas": {"extended_thinking": True}},
            {"gemini": {"unknown": True}},
            {"gemini": {"extended_thinking": "yes"}},
        ):
            response = await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hello"}], "provider_options": options},
            )
            assert response.status_code == 422


@pytest.mark.asyncio
async def test_gemini_webapi_rejects_playwright_options():
    provider = GeminiProvider()
    provider.webapi_adapter.chat_completions = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await provider.chat_completions(
            make_request(model="gemini-3-flash", provider_options={"gemini": {"extended_thinking": True}})
        )
    assert error.value.status_code == 400


def test_gemini_temporary_webapi_rejects_playwright_options():
    with pytest.raises(HTTPException) as error:
        _resolve_temporary_chat_model(
            make_request(model="gemini-3-flash", provider_options={"gemini": {"extended_thinking": True}}),
            MagicMock(),
        )
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_atlas_rejects_gemini_options():
    with pytest.raises(HTTPException) as error:
        await AtlasProvider().chat_completions(
            make_request(model="atlas/model", provider_options={"gemini": {"extended_thinking": True}})
        )
    assert error.value.status_code == 400


@pytest_asyncio.fixture
async def extended_page():
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            pytest.skip(f"Chromium unavailable: {error}")
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_content(
            """
            <button id="picker" aria-label="Open mode picker, currently Flash"></button>
            <div id="menu" role="menu" data-test-id="gem-mode-menu" hidden>
              <gem-menu-item role="menuitem" aria-disabled="false">Extended thinking</gem-menu-item>
            </div>
            <script>
              const picker = document.querySelector('#picker');
              const menu = document.querySelector('#menu');
              const item = document.querySelector('gem-menu-item');
              picker.onclick = () => menu.hidden = false;
              item.onclick = () => {
                const enabled = item.classList.toggle('selected');
                picker.setAttribute('aria-label', `Open mode picker, currently Flash${enabled ? ' Extended' : ''}`);
                menu.hidden = true;
              };
            </script>
            """
        )
        yield page
        await context.close()
        await browser.close()


@pytest.mark.asyncio
async def test_extended_thinking_on_off_and_idempotent(extended_page):
    adapter = GeminiProviderAdapter()
    item = extended_page.get_by_role("menuitem", name="Extended thinking")

    await adapter.set_extended_thinking(extended_page, True)
    assert "selected" in (await item.get_attribute("class") or "").split()
    assert "Extended" in await extended_page.locator("#picker").get_attribute("aria-label")

    await adapter.set_extended_thinking(extended_page, True)
    assert "selected" in (await item.get_attribute("class") or "").split()

    await adapter.set_extended_thinking(extended_page, False)
    assert "selected" not in (await item.get_attribute("class") or "").split()
    assert "Extended" not in await extended_page.locator("#picker").get_attribute("aria-label")

    await adapter.set_extended_thinking(extended_page, False)


@pytest.mark.asyncio
async def test_picker_missing_fails_for_false_and_true(extended_page):
    await extended_page.locator("#picker").evaluate("element => element.remove()")
    adapter = GeminiProviderAdapter()

    with pytest.raises(TransientSessionError):
        await adapter.set_extended_thinking(extended_page, False)
    with pytest.raises(TransientSessionError):
        await adapter.set_extended_thinking(extended_page, True)


@pytest.mark.asyncio
async def test_missing_item_after_menu_render_false_succeeds_true_fails(extended_page):
    picker = MagicMock()
    picker.click = AsyncMock()
    picker.get_attribute = AsyncMock(return_value="Open mode picker, currently Flash")
    item = MagicMock()
    item.first = item
    item.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("missing"))
    extended_page.get_by_role = MagicMock(return_value=item)
    await extended_page.locator("#menu").evaluate("element => element.hidden = false")
    adapter = GeminiProviderAdapter()
    adapter._find_model_picker = AsyncMock(return_value=picker)

    await adapter.set_extended_thinking(extended_page, False)
    with pytest.raises(ModelNotFoundError):
        await adapter.set_extended_thinking(extended_page, True)


@pytest.mark.asyncio
async def test_missing_item_with_extended_label_fails_off(extended_page):
    picker = MagicMock()
    picker.click = AsyncMock()
    picker.get_attribute = AsyncMock(return_value="Open mode picker, currently Flash Extended")
    item = MagicMock()
    item.first = item
    item.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("missing"))
    extended_page.get_by_role = MagicMock(return_value=item)
    await extended_page.locator("#menu").evaluate("element => element.hidden = false")
    adapter = GeminiProviderAdapter()
    adapter._find_model_picker = AsyncMock(return_value=picker)

    with pytest.raises(TransientSessionError, match="normalized off"):
        await adapter.set_extended_thinking(extended_page, False)


@pytest.mark.asyncio
async def test_missing_item_with_disappeared_picker_fails_off(extended_page):
    picker = MagicMock()
    picker.click = AsyncMock()
    item = MagicMock()
    item.first = item
    item.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("missing"))
    extended_page.get_by_role = MagicMock(return_value=item)
    await extended_page.locator("#menu").evaluate("element => element.hidden = false")
    adapter = GeminiProviderAdapter()
    adapter._find_model_picker = AsyncMock(side_effect=[picker, None])

    with pytest.raises(TransientSessionError, match="verified off"):
        await adapter.set_extended_thinking(extended_page, False)


@pytest.mark.asyncio
async def test_mode_menu_not_ready_fails_for_false_and_true(extended_page):
    picker = MagicMock()
    picker.click = AsyncMock()
    menu = MagicMock()
    menu.first = menu
    menu.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("menu missing"))
    extended_page.locator = MagicMock(return_value=menu)
    adapter = GeminiProviderAdapter()
    adapter._find_model_picker = AsyncMock(return_value=picker)

    with pytest.raises(TransientSessionError):
        await adapter.set_extended_thinking(extended_page, False)
    with pytest.raises(TransientSessionError):
        await adapter.set_extended_thinking(extended_page, True)


@pytest.mark.asyncio
async def test_disabled_control_rejects_enable(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => element.setAttribute('aria-disabled', 'true')"
    )
    with pytest.raises(GatedModelError):
        await GeminiProviderAdapter().set_extended_thinking(extended_page, True)


@pytest.mark.asyncio
async def test_disabled_control_already_off_succeeds(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => element.setAttribute('aria-disabled', 'true')"
    )
    await GeminiProviderAdapter().set_extended_thinking(extended_page, False)


@pytest.mark.asyncio
async def test_disabled_selected_control_cannot_be_forced_off(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => { element.setAttribute('aria-disabled', 'true'); element.classList.add('selected'); }"
    )
    with pytest.raises(GatedModelError):
        await GeminiProviderAdapter().set_extended_thinking(extended_page, False)


@pytest.mark.asyncio
async def test_menu_item_render_race_is_waited_out(extended_page):
    await extended_page.locator("gem-menu-item").evaluate("element => element.remove()")
    await extended_page.evaluate(
        """
        () => {
            const picker = document.querySelector('#picker');
            const menu = document.querySelector('#menu');
            picker.onclick = () => setTimeout(() => {
                const item = document.createElement('gem-menu-item');
                item.setAttribute('role', 'menuitem');
                item.setAttribute('aria-disabled', 'false');
                item.textContent = 'Extended thinking';
                item.onclick = () => {
                    const enabled = item.classList.toggle('selected');
                    picker.setAttribute('aria-label', `Open mode picker, currently Flash${enabled ? ' Extended' : ''}`);
                    menu.hidden = true;
                };
                menu.append(item);
                menu.hidden = false;
            }, 50);
        }
        """
    )

    await GeminiProviderAdapter().set_extended_thinking(extended_page, True)
    assert "Extended" in await extended_page.locator("#picker").get_attribute("aria-label")


@pytest.mark.asyncio
async def test_failed_extended_thinking_verification_is_explicit(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => element.onclick = () => { document.querySelector('#menu').hidden = true; }"
    )
    with pytest.raises(TransientSessionError):
        await GeminiProviderAdapter().set_extended_thinking(extended_page, True)


@pytest.mark.asyncio
async def test_request_option_normalization_forces_off_when_omitted_or_false(monkeypatch):
    config = configparser.ConfigParser()
    config["GeminiPlaywright"] = {"extended_thinking": "false"}
    monkeypatch.setattr(playwright_module, "CONFIG", config)

    provider = GeminiProvider()
    browser_adapter = type("Adapter", (), {"set_extended_thinking": AsyncMock()})()

    await provider.playwright_adapter._configure_request_options(
        browser_adapter,
        object(),
        make_request(provider_options={"gemini": {"extended_thinking": True}}),
        None,
    )
    await provider.playwright_adapter._configure_request_options(
        browser_adapter,
        object(),
        make_request(),
        None,
    )
    await provider.playwright_adapter._configure_request_options(
        browser_adapter,
        object(),
        make_request(provider_options={"gemini": {"extended_thinking": False}}),
        None,
    )

    assert [call.args[1] for call in browser_adapter.set_extended_thinking.await_args_list] == [True, False, False]
