from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.browser.session import ProviderSession
from app.services.providers.gemini.auth_selector import GeminiAuthCandidate


def auth_candidate(auth_data, source_type="gemini_config", is_legacy=False):
    return GeminiAuthCandidate(
        source_name="[Gemini] config",
        source_type=source_type,
        auth_data=auth_data,
        is_legacy=is_legacy,
        supports_webapi_cookie_auth=True,
        supports_playwright_storage=True,
        migration_needed=is_legacy,
    )


def make_engine():
    context = MagicMock()
    context.on = MagicMock()
    context.new_page = AsyncMock(return_value=MagicMock())

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    engine = MagicMock()
    engine.max_pages = 2
    engine.browser = browser
    engine.browser_generation = 3
    engine.is_shutting_down = False
    return engine, browser


@pytest.mark.asyncio
async def test_gemini_session_setup_uses_selector_storage_candidate(mocker):
    auth_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "psid", "domain": ".google.com"},
        ],
        "origins": [],
    }
    storage_state = {"cookies": auth_data["cookies"], "origins": []}
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([auth_candidate(auth_data)]),
    )
    translate = mocker.patch(
        "app.services.browser.auth_loader.GeminiAuthStateLoader.translate_to_playwright",
        return_value=storage_state,
    )
    load_fallback = mocker.patch(
        "app.services.browser.auth_loader.GeminiAuthStateLoader.load_auth_state_with_fallback"
    )
    browser_extractor = mocker.patch("app.utils.browser.get_cookie_from_browser")
    client_factory = mocker.patch("app.services.providers.gemini.client.MyGeminiClient")
    engine, browser = make_engine()
    session = ProviderSession(engine, "gemini")
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())

    await session._setup()

    translate.assert_called_once_with(auth_data)
    assert browser.new_context.call_args.kwargs["storage_state"] == storage_state
    load_fallback.assert_not_called()
    browser_extractor.assert_not_called()
    client_factory.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_session_setup_uses_json_candidate_for_storage(mocker):
    json_auth = {
        "cookies": [],
        "origins": [{"origin": "https://gemini.google.com", "localStorage": []}],
    }
    storage_state = {"cookies": [], "origins": json_auth["origins"]}
    candidate = auth_candidate(json_auth, source_type="json_store")
    candidate = GeminiAuthCandidate(
        source_name=candidate.source_name,
        source_type=candidate.source_type,
        auth_data=candidate.auth_data,
        is_legacy=candidate.is_legacy,
        supports_webapi_cookie_auth=False,
        supports_playwright_storage=True,
        migration_needed=candidate.migration_needed,
    )
    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([candidate]),
    )
    mocker.patch(
        "app.services.browser.auth_loader.GeminiAuthStateLoader.translate_to_playwright",
        return_value=storage_state,
    )
    engine, browser = make_engine()
    session = ProviderSession(engine, "gemini")
    mocker.patch.object(session, "close_resources", AsyncMock())
    mocker.patch.object(session, "_eviction_loop", AsyncMock())
    mocker.patch.object(session, "_reaper_loop", AsyncMock())

    await session._setup()

    assert browser.new_context.call_args.kwargs["storage_state"] == storage_state
