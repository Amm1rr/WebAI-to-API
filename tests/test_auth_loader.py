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
    # If canonical is present, it returns it and is_legacy = False
    valid_data = {
        "cookies": [
            {"name": "__Secure-1PSID", "value": "valid_val", "domain": ".google.com"}
        ]
    }
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=valid_data)
    
    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()
    assert loaded == valid_data
    assert is_legacy is False


def test_load_auth_state_with_fallback_legacy(mocker, caplog):
    # 1. Standard __Secure- prefixed keys
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)
    
    mock_config = {
        "Cookies": {
            "__Secure-1PSID": '"legacy_psid"',
            "__Secure-1PSIDTS": "legacy_psidts"
        }
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)

    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()
    assert is_legacy is True
    assert loaded is not None
    
    cookies = loaded["cookies"]
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    assert cookie_dict["__Secure-1PSID"] == "legacy_psid"
    assert cookie_dict["__Secure-1PSIDTS"] == "legacy_psidts"
    
    # 2. Legacy alias keys (gemini_cookie_1psid / gemini_cookie_1psidts)
    mock_config_legacy_alias = {
        "Cookies": {
            "gemini_cookie_1psid": '"alias_psid"',
            "gemini_cookie_1psidts": "alias_psidts"
        }
    }
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config_legacy_alias)

    loaded_alias, is_legacy_alias = GeminiAuthStateLoader.load_auth_state_with_fallback()
    assert is_legacy_alias is True
    assert loaded_alias is not None
    
    cookies_alias = loaded_alias["cookies"]
    cookie_dict_alias = {c["name"]: c["value"] for c in cookies_alias}
    assert cookie_dict_alias["__Secure-1PSID"] == "alias_psid"
    assert cookie_dict_alias["__Secure-1PSIDTS"] == "alias_psidts"
    
    # Verify deprecation warning logged
    assert any("Loaded deprecated cookies from config.conf" in record.message for record in caplog.records)


def test_load_auth_state_with_fallback_missing_all(mocker):
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)
    mocker.patch("app.services.browser.auth_loader.CONFIG", {})
    
    loaded, is_legacy = GeminiAuthStateLoader.load_auth_state_with_fallback()
    assert loaded is None
    assert is_legacy is False


def test_auth_manager_migration_needed_status(mocker):
    auth_mgr = get_auth_manager()
    
    # 1. When canonical is active
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value={"cookies": []})
    auth_mgr.refresh_status()
    status = auth_mgr.get_status()
    assert "migration_needed" not in status["playwright"]
    assert "legacy_fallback_active" not in status["playwright"]

    # 2. When legacy is active
    mocker.patch.object(GeminiAuthStateLoader, "load_canonical_state", return_value=None)
    mock_config = {
        "Cookies": {
            "__Secure-1PSID": '"legacy_psid"',
            "__Secure-1PSIDTS": "legacy_psidts"
        },
        "Playwright": {}
    }
    mocker.patch("app.services.browser.auth_manager.CONFIG", mock_config)
    mocker.patch("app.services.browser.auth_loader.CONFIG", mock_config)
    
    auth_mgr.refresh_status()
    status = auth_mgr.get_status()
    assert status["playwright"]["legacy_fallback_active"] is True
    assert status["playwright"]["migration_needed"] is True
