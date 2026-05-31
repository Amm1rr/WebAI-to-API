import pytest

from app.services.browser.auth_loader import GeminiAuthStateLoader
from app.services.providers.gemini.auth_selector import GeminiAuthSelector


def auth_data(psid, psidts="psidts"):
    return {
        "cookies": [
            {"name": "__Secure-1PSID", "value": psid, "domain": ".google.com"},
            {"name": "__Secure-1PSIDTS", "value": psidts, "domain": ".google.com"},
        ],
        "origins": [],
    }


def patch_sources(mocker, gemini=None, legacy=None, json_source=None):
    return (
        mocker.patch.object(
            GeminiAuthStateLoader,
            "get_gemini_config_source",
            return_value=(gemini, False),
        ),
        mocker.patch.object(
            GeminiAuthStateLoader,
            "get_legacy_cookie_source",
            return_value=(legacy, legacy is not None),
        ),
        mocker.patch.object(
            GeminiAuthStateLoader,
            "get_json_source",
            return_value=(json_source, False),
        ),
    )


def test_iter_candidates_orders_gemini_legacy_json(mocker):
    patch_sources(
        mocker,
        gemini=auth_data("gemini_psid"),
        legacy=auth_data("legacy_psid"),
        json_source=auth_data("json_psid"),
    )

    candidates = list(GeminiAuthSelector.iter_candidates())

    assert [candidate.source_type for candidate in candidates] == [
        "gemini_config",
        "legacy_cookies",
        "json_store",
    ]
    assert [candidate.source_name for candidate in candidates] == [
        "[Gemini] config",
        "[Cookies] legacy config",
        "gemini.json canonical store",
    ]


def test_canonical_gemini_candidate_has_priority(mocker):
    patch_sources(
        mocker,
        gemini=auth_data("gemini_psid"),
        legacy=auth_data("legacy_psid"),
        json_source=auth_data("json_psid"),
    )

    first_candidate = next(GeminiAuthSelector.iter_candidates())

    assert first_candidate.source_type == "gemini_config"
    assert first_candidate.auth_data == auth_data("gemini_psid")
    assert first_candidate.is_legacy is False
    assert first_candidate.migration_needed is False
    assert first_candidate.supports_webapi_cookie_auth is True
    assert first_candidate.supports_playwright_storage is True


def test_legacy_candidate_metadata_preserved(mocker):
    patch_sources(
        mocker,
        legacy=auth_data("legacy_psid"),
        json_source=auth_data("json_psid"),
    )

    candidate = next(GeminiAuthSelector.iter_candidates())

    assert candidate.source_type == "legacy_cookies"
    assert candidate.is_legacy is True
    assert candidate.migration_needed is True
    assert candidate.supports_webapi_cookie_auth is True
    assert candidate.supports_playwright_storage is True


def test_json_fallback_candidate_available_for_playwright_storage(mocker):
    json_auth = {
        "cookies": [],
        "origins": [{"origin": "https://gemini.google.com", "localStorage": []}],
    }
    patch_sources(mocker, json_source=json_auth)

    candidate = next(GeminiAuthSelector.iter_candidates())

    assert candidate.source_type == "json_store"
    assert candidate.auth_data == json_auth
    assert candidate.is_legacy is False
    assert candidate.migration_needed is False
    assert candidate.supports_webapi_cookie_auth is False
    assert candidate.supports_playwright_storage is True


def test_missing_sources_are_skipped(mocker):
    gemini_source, legacy_source, json_source = patch_sources(
        mocker,
        gemini=None,
        legacy=auth_data("legacy_psid"),
        json_source=None,
    )

    candidates = list(GeminiAuthSelector.iter_candidates())

    assert [candidate.source_type for candidate in candidates] == ["legacy_cookies"]
    gemini_source.assert_called_once()
    legacy_source.assert_called_once()
    json_source.assert_called_once()


def test_selector_does_not_validate_account_status_or_create_clients(mocker):
    patch_sources(mocker, gemini=auth_data("gemini_psid"))
    client_factory = mocker.patch("app.services.providers.gemini.client.MyGeminiClient")

    candidates = list(GeminiAuthSelector.iter_candidates())

    assert len(candidates) == 1
    client_factory.assert_not_called()


def test_selector_does_not_call_browser_cookie_extraction(mocker):
    patch_sources(mocker, gemini=auth_data("gemini_psid"))
    browser_extractor = mocker.patch("app.utils.browser.get_cookie_from_browser")

    candidates = list(GeminiAuthSelector.iter_candidates())

    assert len(candidates) == 1
    browser_extractor.assert_not_called()
