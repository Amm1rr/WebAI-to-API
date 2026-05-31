import os
import json
import pytest
from app.config import CONFIG
from app.services.browser.auth_loader import GeminiAuthStateLoader
from app.services.browser.auth_manager import get_auth_manager, AuthStatus

def test_validate_state_structure():
    # Valid structure
    valid_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "some_value", "domain": ".google.com", "path": "/"},
            {"name": "__Secure-1PSIDTS", "value": "another_value", "domain": ".google.com", "path": "/"}
        ],
        "origins": []
    }
    assert GeminiAuthStateLoader.validate_state_structure(valid_data) is True

    # Missing cookies list
    assert GeminiAuthStateLoader.validate_state_structure({"origins": []}) is False

    # Cookies not a list
    assert GeminiAuthStateLoader.validate_state_structure({"cookies": "not_a_list"}) is False

    # Not a dictionary
    assert GeminiAuthStateLoader.validate_state_structure("not_a_dict") is False

    # Missing name/value in cookie dict
    invalid_cookie = {
        "cookies": [
            {"name": "only_name"}
        ]
    }
    assert GeminiAuthStateLoader.validate_state_structure(invalid_cookie) is False


def test_translate_to_playwright():
    data = {
        "cookies": [
            {"name": "foo", "value": "bar"}
        ],
        "origins": [
            {"origin": "http://foo.com", "localStorage": []}
        ]
    }
    result = GeminiAuthStateLoader.translate_to_playwright(data)
    assert result["cookies"] == [{"name": "foo", "value": "bar"}]
    assert result["origins"] == [{"origin": "http://foo.com", "localStorage": []}]

    # When missing origins or cookies
    empty_result = GeminiAuthStateLoader.translate_to_playwright({})
    assert empty_result["cookies"] == []
    assert empty_result["origins"] == []


def test_translate_to_webapi():
    data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "psid_val", "domain": ".google.com"},
            {"name": "__Secure-1PSIDTS", "value": "psidts_val", "domain": "google.com"},
            {"name": "other_cookie", "value": "other_val", "domain": "example.com"}
        ]
    }
    extracted, psid, psidts = GeminiAuthStateLoader.translate_to_webapi(data)
    assert extracted["__Secure-1PSID"] == "psid_val"
    assert extracted["__Secure-1PSIDTS"] == "psidts_val"
    assert "other_cookie" not in extracted
    assert psid == "psid_val"
    assert psidts == "psidts_val"


def test_load_canonical_state_missing_or_empty(tmp_path, mocker):
    # Mock get_canonical_path to a non-existent path
    mocker.patch.object(GeminiAuthStateLoader, "get_canonical_path", return_value=str(tmp_path / "missing.json"))
    assert GeminiAuthStateLoader.load_canonical_state() is None

    # Empty file
    empty_file = tmp_path / "empty.json"
    empty_file.touch()
    mocker.patch.object(GeminiAuthStateLoader, "get_canonical_path", return_value=str(empty_file))
    assert GeminiAuthStateLoader.load_canonical_state() is None

    # Invalid JSON
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("invalid json")
    mocker.patch.object(GeminiAuthStateLoader, "get_canonical_path", return_value=str(invalid_file))
    assert GeminiAuthStateLoader.load_canonical_state() is None

    # Valid JSON but invalid structure
    bad_structure = tmp_path / "bad_structure.json"
    bad_structure.write_text(json.dumps({"wrong": "keys"}))
    mocker.patch.object(GeminiAuthStateLoader, "get_canonical_path", return_value=str(bad_structure))
    assert GeminiAuthStateLoader.load_canonical_state() is None


def test_load_canonical_state_valid(tmp_path, mocker):
    valid_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "valid_val", "domain": ".google.com"}
        ]
    }
    valid_file = tmp_path / "valid.json"
    valid_file.write_text(json.dumps(valid_data))
    mocker.patch.object(GeminiAuthStateLoader, "get_canonical_path", return_value=str(valid_file))
    
    loaded = GeminiAuthStateLoader.load_canonical_state()
    assert loaded == valid_data


def test_load_auth_state_with_fallback_canonical(tmp_path, mocker):
    # If canonical is present and no [Gemini] config, it returns JSON and is_legacy = False
    valid_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "valid_val", "domain": ".google.com"}
        ]
    }
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=valid_data)
    # Ensure no [Gemini] config interferes
    mocker.patch("app.services.browser.auth_loader.CONFIG", {})

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()
    assert loaded == valid_data
    assert is_legacy is False


def test_load_auth_state_with_fallback_legacy(mocker, caplog):
    # Test legacy alias keys (gemini_cookie_1psid / gemini_cookie_1psidts)
    # NOTE: __Secure- prefixed keys under [Cookies] are no longer supported
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Cookies": {
            "gemini_cookie_1psid": '"alias_psid"',
            "gemini_cookie_1psidts": "alias_psidts"
        }
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()
    assert is_legacy is True
    assert loaded is not None

    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "alias_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "alias_psidts"

    # Verify deprecation warning logged (updated message)
    assert any("Legacy Gemini cookie configuration" in record.message for record in caplog.records)


def test_load_auth_state_with_fallback_missing_all(mocker):
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)
    mocker.patch("app.services.browser.auth_loader.CONFIG", {})
    
    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()
    assert loaded is None
    assert is_legacy is False


def test_auth_manager_migration_needed_status(mocker):
    from unittest.mock import patch
    from app.services.providers.gemini.auth import GeminiAuthStrategy

    auth_mgr = get_auth_manager()
    # Set strategy (required for refresh_status to work properly)
    auth_mgr.set_strategy(GeminiAuthStrategy())

    # 1. When canonical is active
    canonical_mock = mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value={"cookies": []})
    # Use empty CONFIG for this step
    empty_config = {"Gemini": {}, "Cookies": {}, "Playwright": {}}
    with patch("app.services.browser.auth_loader.CONFIG", empty_config):
        with patch("app.services.providers.gemini.auth.CONFIG", empty_config):
            auth_mgr.refresh_status()
            status = auth_mgr.get_status()
            assert "migration_needed" not in status["playwright"]
            assert "legacy_fallback_active" not in status["playwright"]

    # 2. When legacy is active (using legacy aliases)
    canonical_mock.return_value = None

    # Need to patch at the import location used by gemini/auth.py
    mock_config = {
        "Gemini": {},  # Empty [Gemini] section so it falls through to [Cookies]
        "Cookies": {
            "gemini_cookie_1psid": '"legacy_psid"',
            "gemini_cookie_1psidts": "legacy_psidts"
        },
        "Playwright": {}
    }

    # Patch at the module level where CONFIG is imported
    with patch("app.services.browser.auth_loader.CONFIG", mock_config):
        with patch("app.services.providers.gemini.auth.CONFIG", mock_config):
            auth_mgr.refresh_status()
            status = auth_mgr.get_status()

            assert status["playwright"]["legacy_fallback_active"] is True
            assert status["playwright"]["migration_needed"] is True


# =============================================================================
# New Tests: Gemini Cookie Configuration Modernization
# =============================================================================

def test_gemini_canonical_format(mocker, caplog):
    """
    Test A: Canonical Format
    Test loading from [Gemini] section with __Secure-1PSID and __Secure-1PSIDTS.
    Verify no deprecation warning and correct cookie extraction.
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Gemini": {
            "backend": "webapi",
            "default_model": "gemini-3-flash",
            "__Secure-1PSID": '"test_psid"',
            "__Secure-1PSIDTS": "test_psidts"
        },
        "Cookies": {},
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    assert is_legacy is False
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "test_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "test_psidts"

    # Verify NO deprecation warning logged for canonical format
    assert not any("Legacy Gemini cookie configuration" in record.message for record in caplog.records)


def test_gemini_legacy_format(mocker, caplog):
    """
    Test B: Legacy Format
    Test loading from [Cookies] with legacy aliases gemini_cookie_1psid/gemini_cookie_1psidts.
    Verify deprecation warning is logged and correct cookie extraction.
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Gemini": {},
        "Cookies": {
            "gemini_cookie_1psid": '"legacy_psid"',
            "gemini_cookie_1psidts": "legacy_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    assert is_legacy is True
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "legacy_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "legacy_psidts"

    # Verify deprecation warning IS logged for legacy format
    assert any("Legacy Gemini cookie configuration" in record.message for record in caplog.records)


def test_fallback_priority_gemini_empty_to_cookies(mocker):
    """
    Test C.1: Fallback Priority - [Gemini] with empty values → falls back to [Cookies]
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Gemini": {
            "__Secure-1PSID": "",
            "__Secure-1PSIDTS": ""
        },
        "Cookies": {
            "gemini_cookie_1psid": '"fallback_psid"',
            "gemini_cookie_1psidts": "fallback_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should fall back to [Cookies]
    assert is_legacy is True
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "fallback_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "fallback_psidts"


def test_fallback_priority_gemini_missing_to_cookies(mocker):
    """
    Test C.2: Fallback Priority - [Gemini] missing from config → falls back to [Cookies]
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Cookies": {
            "gemini_cookie_1psid": '"fallback_psid"',
            "gemini_cookie_1psidts": "fallback_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should fall back to [Cookies]
    assert is_legacy is True
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "fallback_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "fallback_psidts"


def test_fallback_priority_both_empty_to_json(mocker):
    """
    Test C.3: Fallback Priority - Both empty/missing → falls back to gemini.json
    """
    valid_json_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "json_psid", "domain": ".google.com"}
        ]
    }
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=valid_json_data)

    mock_config = {
        "Gemini": {
            "__Secure-1PSID": "",
            "__Secure-1PSIDTS": ""
        },
        "Cookies": {
            "gemini_cookie_1psid": "",
            "gemini_cookie_1psidts": ""
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should fall back to gemini.json
    assert is_legacy is False
    assert loaded is not None
    assert loaded == valid_json_data


def test_no_sources_available(mocker):
    """
    Test D: No Sources
    All sources missing/empty → returns None, is_legacy=False
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Gemini": {},
        "Cookies": {},
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    assert loaded is None
    assert is_legacy is False


def test_backward_compatibility_legacy_cookies(mocker, caplog):
    """
    Test E: Backward Compatibility
    Existing users with [Cookies] config continue to work without breaking.
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    # Simulate existing user with legacy config
    mock_config = {
        "Cookies": {
            "gemini_cookie_1psid": '"existing_user_psid"',
            "gemini_cookie_1psidts": "existing_user_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should work exactly as before
    assert is_legacy is True
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "existing_user_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "existing_user_psidts"

    # Deprecation warning should be logged
    assert any("Legacy Gemini cookie configuration" in record.message for record in caplog.records)


def test_partial_configuration_gemini_only_psid(mocker):
    """
    Test F.1: Partial Configuration - [Gemini] with only __Secure-1PSID
    Should treat as incomplete and fallback to next source.
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Gemini": {
            "__Secure-1PSID": '"partial_psid"',
            "__Secure-1PSIDTS": ""
        },
        "Cookies": {
            "gemini_cookie_1psid": '"fallback_psid"',
            "gemini_cookie_1psidts": "fallback_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should fall back to [Cookies] because [Gemini] is incomplete
    assert is_legacy is True
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "fallback_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "fallback_psidts"


def test_partial_configuration_gemini_only_psidts(mocker):
    """
    Test F.2: Partial Configuration - [Gemini] with only __Secure-1PSIDTS
    Should treat as incomplete and fallback to next source.
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Gemini": {
            "__Secure-1PSID": "",
            "__Secure-1PSIDTS": "partial_psidts"
        },
        "Cookies": {
            "gemini_cookie_1psid": '"fallback_psid"',
            "gemini_cookie_1psidts": "fallback_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should fall back to [Cookies] because [Gemini] is incomplete
    assert is_legacy is True
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "fallback_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "fallback_psidts"


def test_partial_configuration_cookies_only_psid(mocker):
    """
    Test F.3: Partial Configuration - [Cookies] with only gemini_cookie_1psid
    Should treat as incomplete and fallback to next source.
    """
    valid_json_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "json_psid", "domain": ".google.com"}
        ]
    }
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=valid_json_data)

    mock_config = {
        "Gemini": {
            "__Secure-1PSID": "",
            "__Secure-1PSIDTS": ""
        },
        "Cookies": {
            "gemini_cookie_1psid": '"partial_psid"',
            "gemini_cookie_1psidts": ""
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should fall back to gemini.json because [Cookies] is incomplete
    assert is_legacy is False
    assert loaded is not None
    assert loaded == valid_json_data


def test_partial_configuration_cookies_only_psidts(mocker):
    """
    Test F.4: Partial Configuration - [Cookies] with only gemini_cookie_1psidts
    Should treat as incomplete and fallback to next source.
    """
    valid_json_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "json_psid", "domain": ".google.com"}
        ]
    }
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=valid_json_data)

    mock_config = {
        "Gemini": {
            "__Secure-1PSID": "",
            "__Secure-1PSIDTS": ""
        },
        "Cookies": {
            "gemini_cookie_1psid": "",
            "gemini_cookie_1psidts": "partial_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should fall back to gemini.json because [Cookies] is incomplete
    assert is_legacy is False
    assert loaded is not None
    assert loaded == valid_json_data


def test_partial_configuration_gemini_both_present_valid(mocker):
    """
    Test F.5: Partial Configuration - [Gemini] with both present and non-empty
    Should use the source (valid complete configuration).
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Gemini": {
            "__Secure-1PSID": '"complete_psid"',
            "__Secure-1PSIDTS": "complete_psidts"
        },
        "Cookies": {},
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should use [Gemini] source (complete configuration)
    assert is_legacy is False
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "complete_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "complete_psidts"


def test_partial_configuration_cookies_both_present_valid(mocker, caplog):
    """
    Test F.6: Partial Configuration - [Cookies] with both present and non-empty
    Should use the source (valid complete legacy configuration).
    """
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)

    mock_config = {
        "Gemini": {},
        "Cookies": {
            "gemini_cookie_1psid": '"complete_legacy_psid"',
            "gemini_cookie_1psidts": "complete_legacy_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should use [Cookies] source (complete legacy configuration)
    assert is_legacy is True
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "complete_legacy_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "complete_legacy_psidts"

    # Deprecation warning should be logged
    assert any("Legacy Gemini cookie configuration" in record.message for record in caplog.records)


def test_gemini_priority_over_json(mocker):
    """
    Test: [Gemini] section has priority over runtime/auth/gemini.json
    """
    valid_json_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "json_psid", "domain": ".google.com"}
        ]
    }
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=valid_json_data)

    mock_config = {
        "Gemini": {
            "__Secure-1PSID": '"config_psid"',
            "__Secure-1PSIDTS": "config_psidts"
        },
        "Cookies": {},
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should use [Gemini] (higher priority than JSON)
    assert is_legacy is False
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "config_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "config_psidts"
    # Should NOT be the JSON data
    assert loaded != valid_json_data


def test_config_case_preservation(tmp_path, mocker):
    """
    Test: Verify ConfigParser preserves case for __Secure-1PSID/__Secure-1PSIDTS keys.
    This ensures the implementation works correctly with real parsed config files.
    """
    import configparser

    # Create a test config file with exact case
    test_config_content = """
[Gemini]
__Secure-1PSID = "test_psid"
__Secure-1PSIDTS = test_psidts

[Cookies]
gemini_cookie_1psid = "legacy_psid"
gemini_cookie_1psidts = legacy_psidts
"""
    config_file = tmp_path / "test_config.conf"
    config_file.write_text(test_config_content)

    # Load config using the same method as the application
    config = configparser.ConfigParser()
    config.optionxform = str  # Preserve case (same as in config.py)
    config.read(str(config_file), encoding="utf-8")

    # Verify keys are preserved with exact case
    assert "Gemini" in config
    assert "__Secure-1PSID" in config["Gemini"]
    assert "__Secure-1PSIDTS" in config["Gemini"]
    assert config["Gemini"]["__Secure-1PSID"] == '"test_psid"'
    assert config["Gemini"]["__Secure-1PSIDTS"] == "test_psidts"

    # Verify legacy keys are also preserved
    assert "Cookies" in config
    assert "gemini_cookie_1psid" in config["Cookies"]
    assert "gemini_cookie_1psidts" in config["Cookies"]

    # Now test with auth_loader using the parsed config
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)
    mocker.patch("app.services.browser.auth_loader.CONFIG", config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()

    # Should load from [Gemini] section with exact case keys
    assert is_legacy is False
    assert loaded is not None
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "test_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "test_psidts"
