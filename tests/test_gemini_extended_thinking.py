import configparser
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from types import SimpleNamespace
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
from app.services.providers.gemini import shared as shared_module
from app.services.providers.gemini.session_manager import SessionRegistry, SessionManager
from app.services.providers.gemini.persistence import serialize_session_state
from app.services.providers.gemini.client import GeminiGenerationUnavailableError


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
        config["Gemini"] = {"extended_thinking": config_value}
    monkeypatch.setattr(shared_module, "CONFIG", config)

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
async def test_missing_gemini_config_key_defaults_off(monkeypatch):
    config = configparser.ConfigParser()
    config["Gemini"] = {}
    monkeypatch.setattr(shared_module, "CONFIG", config)

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
async def test_legacy_gemini_playwright_config_has_no_effect(monkeypatch):
    config = configparser.ConfigParser()
    config["GeminiPlaywright"] = {"extended_thinking": "true"}
    monkeypatch.setattr(shared_module, "CONFIG", config)

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


def test_gemini_temporary_webapi_rejects_playwright_options():
    with pytest.raises(HTTPException) as error:
        _resolve_temporary_chat_model(
            make_request(model="gemini-3-flash", provider_options={"gemini": {"extended_thinking": True}}),
            MagicMock(),
        )
    assert error.value.status_code == 400


def _make_webapi_mocks(mocker, registry=None):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    if registry is None:
        registry = mocker.Mock(spec=SessionRegistry)
    mock_registry = registry
    mock_manager = mocker.Mock(spec=SessionManager)
    mock_manager.get_response_stateful = mocker.AsyncMock(
        return_value=(SimpleNamespace(text="response"), True)
    )
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()
    return mock_client, mock_registry, mock_manager


def _make_webapi_request(**kwargs):
    from app.schemas.request import OpenAIChatRequest
    defaults = {
        "messages": [{"role": "user", "content": "hello"}],
        "model": "gemini-3-flash",
        "conversation_id": "test_token_XYZ",
        "stream": False,
    }
    defaults.update(kwargs)
    return OpenAIChatRequest(**defaults)


@pytest.mark.asyncio
async def test_webapi_buffered_request_override_true(mocker, install_gemini_client):
    mock_client, mock_registry, mock_manager = _make_webapi_mocks(mocker)
    install_gemini_client(mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    provider = GeminiProvider()
    result = await provider.chat_completions(
        _make_webapi_request(provider_options={"gemini": {"extended_thinking": True}})
    )

    assert result["choices"][0]["message"]["content"] == "response"
    mock_manager.get_response_stateful.assert_awaited_once()
    assert mock_manager.get_response_stateful.await_args.kwargs["extended_thinking"] is True


@pytest.mark.asyncio
async def test_webapi_buffered_request_override_false(mocker, install_gemini_client, monkeypatch):
    config = configparser.ConfigParser()
    config["Gemini"] = {"extended_thinking": "true"}
    monkeypatch.setattr(shared_module, "CONFIG", config)

    mock_client, mock_registry, mock_manager = _make_webapi_mocks(mocker)
    install_gemini_client(mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    provider = GeminiProvider()
    await provider.chat_completions(
        _make_webapi_request(provider_options={"gemini": {"extended_thinking": False}})
    )

    assert mock_manager.get_response_stateful.await_args.kwargs["extended_thinking"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_value", "expected"),
    [("true", True), ("false", False)],
)
async def test_webapi_buffered_fallback_to_shared_config(mocker, install_gemini_client, monkeypatch, config_value, expected):
    config = configparser.ConfigParser()
    config["Gemini"] = {"extended_thinking": config_value}
    monkeypatch.setattr(shared_module, "CONFIG", config)

    mock_client, mock_registry, mock_manager = _make_webapi_mocks(mocker)
    install_gemini_client(mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    provider = GeminiProvider()
    await provider.chat_completions(_make_webapi_request())

    assert mock_manager.get_response_stateful.await_args.kwargs["extended_thinking"] is expected


@pytest.mark.asyncio
async def test_webapi_progressive_streaming_propagation(mocker, install_gemini_client):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)

    received = {}

    async def mock_generator(*args, **kwargs):
        received.update(kwargs)
        yield {"type": "chunk", "text_delta": "delta", "is_reused": True}
        yield {
            "type": "final",
            "response": SimpleNamespace(text="response", images=[], videos=[], media=[], thoughts="hidden"),
            "is_reused": True,
        }

    mock_manager.get_streaming_response_stateful = mock_generator
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()

    install_gemini_client(mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    provider = GeminiProvider()
    response = await provider.chat_completions(
        _make_webapi_request(
            stream=True,
            provider_options={"gemini": {"extended_thinking": True}},
        )
    )
    assert response is not None
    async for _ in response.body_iterator:
        pass
    assert received["extended_thinking"] is True


@pytest.mark.asyncio
async def test_session_manager_progressive_passes_extended_thinking_to_upstream(mocker, install_registry_generation):
    client = mocker.Mock()
    session = mocker.Mock()
    received = {}

    async def send_message_stream(**kwargs):
        received.update(kwargs)
        yield SimpleNamespace(text_delta="delta")

    session.send_message_stream = send_message_stream
    client.start_chat.return_value = session
    generation = install_registry_generation(client)
    manager = SessionManager(client, generation)

    async for _ in manager.get_streaming_response_stateful(
        "model",
        [{"content": "hello"}],
        "",
        extended_thinking=True,
    ):
        pass

    assert received["extended_thinking"] is True


@pytest.mark.asyncio
async def test_webapi_tool_call_buffered_path_propagation(mocker, install_gemini_client):
    mock_client, mock_registry, mock_manager = _make_webapi_mocks(mocker)
    install_gemini_client(mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    provider = GeminiProvider()
    response = await provider.chat_completions(
        _make_webapi_request(
            stream=True,
            tools=[{"type": "function", "function": {"name": "lookup", "description": "look up"}}],
            provider_options={"gemini": {"extended_thinking": True}},
        )
    )
    assert response is not None
    mock_manager.get_response_stateful.assert_awaited_once()
    assert mock_manager.get_response_stateful.await_args.kwargs["extended_thinking"] is True


@pytest.mark.asyncio
async def test_webapi_generation_retry_preserves_extended_thinking(mocker, install_gemini_client):
    mock_client = mocker.Mock()
    mock_client.client.account_status.name = "AVAILABLE"
    mock_client.resolve_model.return_value = SimpleNamespace(model_name="gemini-3-flash", is_available=True)
    mock_registry = mocker.Mock(spec=SessionRegistry)
    mock_manager = mocker.Mock(spec=SessionManager)
    mock_manager.get_response_stateful = mocker.AsyncMock(
        side_effect=[GeminiGenerationUnavailableError("generation retired"), (SimpleNamespace(text="response"), True)]
    )
    mock_registry.get_session = mocker.AsyncMock(return_value=mock_manager)
    mock_registry.save_session_snapshot = mocker.AsyncMock()

    install_gemini_client(mock_client)
    mocker.patch("app.services.providers.gemini.webapi_adapter.get_gemini_chat_registry", return_value=mock_registry)

    provider = GeminiProvider()
    result = await provider.chat_completions(
        _make_webapi_request(provider_options={"gemini": {"extended_thinking": True}})
    )

    assert result["choices"][0]["message"]["content"] == "response"
    calls = mock_manager.get_response_stateful.await_args_list
    assert len(calls) == 2
    assert all(call.kwargs["extended_thinking"] is True for call in calls)


@pytest.mark.asyncio
async def test_reused_session_switches_extended_thinking_without_rebuild(mocker, install_registry_generation):
    client = mocker.Mock()
    session = mocker.Mock()
    received = []
    session.send_message = mocker.AsyncMock(side_effect=lambda **kwargs: received.append(kwargs.get("extended_thinking")) or SimpleNamespace(text="ok"))
    client.start_chat.return_value = session
    generation = install_registry_generation(client)
    registry = SessionRegistry(client, generation=generation)
    manager = await registry.get_session("reused-conversation")

    await manager.get_response_stateful("model", [{"content": "first"}], "", extended_thinking=True)
    await manager.get_response_stateful("model", [{"content": "second"}], "", extended_thinking=False)

    assert received == [True, False]
    client.start_chat.assert_called_once_with(model="model", gem=None)
    assert manager.session is session


def test_session_snapshot_state_has_no_extended_thinking():
    session = SimpleNamespace(
        gem=None,
        model="gemini-3-flash",
        metadata=["cid", "rid", "rcid", "context"],
    )
    payload = serialize_session_state(session)
    assert "extended_thinking" not in payload

    manager = SessionManager(None, client_generation=0)
    assert not hasattr(manager, "extended_thinking")


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
            <button id="picker" data-test-id="bard-mode-menu-button" aria-label="Open mode picker, currently Flash"></button>
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
    await extended_page.locator("gem-menu-item").evaluate(
        "element => element.setAttribute('data-mode-id', 'decoy')"
    )
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
async def test_missing_item_with_extended_label_succeeds_off(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => element.setAttribute('data-mode-id', 'decoy')"
    )
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

    await adapter.set_extended_thinking(extended_page, False)


@pytest.mark.asyncio
async def test_missing_item_succeeds_off_without_secondary_picker_verification(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => element.setAttribute('data-mode-id', 'decoy')"
    )
    item = MagicMock()
    item.first = item
    item.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("missing"))
    extended_page.get_by_role = MagicMock(return_value=item)
    await extended_page.locator("#menu").evaluate("element => element.hidden = false")
    adapter = GeminiProviderAdapter()

    await adapter.set_extended_thinking(extended_page, False)


@pytest.mark.asyncio
async def test_absent_control_false_succeeds_true_capability_error(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => { element.textContent = 'Other'; element.removeAttribute('role'); }"
    )
    adapter = GeminiProviderAdapter()

    await adapter.set_extended_thinking(extended_page, False)
    with pytest.raises(ModelNotFoundError):
        await adapter.set_extended_thinking(extended_page, True)


@pytest.mark.asyncio
async def test_playwright_failure_during_control_check_still_fails(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => element.setAttribute('data-mode-id', 'decoy')"
    )
    item = MagicMock()
    item.first = item
    item.wait_for = AsyncMock(side_effect=PlaywrightError("Target closed"))
    extended_page.get_by_role = MagicMock(return_value=item)
    adapter = GeminiProviderAdapter()

    with pytest.raises(PlaywrightError):
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
    config["Gemini"] = {"extended_thinking": "false"}
    monkeypatch.setattr(shared_module, "CONFIG", config)

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


@pytest.mark.asyncio
async def test_mode_picker_found_via_test_id_without_english_label(extended_page):
    await extended_page.locator("#picker").evaluate("element => element.removeAttribute('aria-label')")
    assert await GeminiProviderAdapter()._find_model_picker(extended_page) is not None


@pytest.mark.asyncio
async def test_structural_detection_toggles_without_english_text(extended_page):
    await extended_page.evaluate(
        """
        () => {
            const menu = document.querySelector('#menu');
            const item = document.querySelector('gem-menu-item');
            item.textContent = 'Erweitertes Denken';
            const picker = document.querySelector('#picker');
            picker.setAttribute('aria-label', 'Modusauswahl, derzeit Flash');
            item.onclick = () => {
                item.classList.toggle('selected');
                menu.hidden = true;
            };
        }
        """
    )
    adapter = GeminiProviderAdapter()
    item = extended_page.locator("gem-menu-item")

    await adapter.set_extended_thinking(extended_page, True)
    assert "selected" in ((await item.get_attribute("class")) or "").split()

    await adapter.set_extended_thinking(extended_page, False)
    assert "selected" not in ((await item.get_attribute("class")) or "").split()


@pytest.mark.asyncio
async def test_multiple_modeless_items_rejected_structural_uses_english_fallback(extended_page):
    await extended_page.evaluate(
        """
        () => {
            const menu = document.querySelector('#menu');
            const decoy = document.createElement('gem-menu-item');
            decoy.setAttribute('role', 'menuitem');
            decoy.textContent = 'Anderer Modus';
            menu.append(decoy);
        }
        """
    )
    real = extended_page.locator("gem-menu-item").first

    await GeminiProviderAdapter().set_extended_thinking(extended_page, True)
    assert "selected" in ((await real.get_attribute("class")) or "").split()

    await GeminiProviderAdapter().set_extended_thinking(extended_page, False)
    assert "selected" not in ((await real.get_attribute("class")) or "").split()


@pytest.mark.asyncio
async def test_no_modeless_candidate_uses_english_fallback(extended_page):
    await extended_page.locator("gem-menu-item").evaluate(
        "element => element.setAttribute('data-mode-id', 'decoy')"
    )

    # false + present-but-off succeeds via fallback; true would toggle it.
    await GeminiProviderAdapter().set_extended_thinking(extended_page, False)
    await GeminiProviderAdapter().set_extended_thinking(extended_page, True)
    assert "selected" in (
        (await extended_page.locator("gem-menu-item").get_attribute("class")) or ""
    ).split()
