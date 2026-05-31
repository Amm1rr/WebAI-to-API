# src/app/services/browser/auth_loader.py
import os
import json
from typing import Dict, List, Any, Optional, Tuple
from app.config import CONFIG, get_default_auth_state_dir
from app.logger import logger

_legacy_gemini_cookie_warning_emitted = False


def _warn_legacy_gemini_cookie_config_once() -> None:
    global _legacy_gemini_cookie_warning_emitted
    if _legacy_gemini_cookie_warning_emitted:
        return

    logger.warning(
        "Legacy Gemini cookie configuration detected in [Cookies]. "
        "Please move cookies to the [Gemini] section. "
        "Support will be removed in a future release."
    )
    _legacy_gemini_cookie_warning_emitted = True


class GeminiAuthStateLoader:
    """
    Stateless loader and translator for Gemini authentication and session state.
    Responsible for loading, validating, and translating session data
    from the canonical JSON store.
    """

    @classmethod
    def get_canonical_path(cls) -> str:
        """Get the absolute path to the canonical gemini.json store."""
        auth_state_dir = CONFIG["Playwright"].get("auth_state_dir", get_default_auth_state_dir())
        return os.path.join(auth_state_dir, "gemini.json")

    @classmethod
    def load_canonical_state(cls) -> Optional[Dict[str, Any]]:
        """
        Loads the canonical auth state from gemini.json.
        Returns the parsed dictionary if valid, otherwise None.
        """
        path = cls.get_canonical_path()
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if cls.validate_state_structure(data):
                return data
        except Exception as e:
            logger.error(f"GeminiAuthStateLoader: Failed to parse canonical state at {path}: {e}")
        return None

    @classmethod
    def load_auth_state_with_fallback(cls) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Loads authentication cookies utilizing the prioritized hierarchy.
        Priority 1: Load from [Gemini] section in config.conf (canonical format).
        Priority 2: Load from legacy [Cookies] in config.conf (logs deprecation).
        Priority 3: Load from canonical store runtime/auth/gemini.json.

        Returns Tuple (cookies_data_dict, is_legacy_fallback)

        Note: Fallback occurs ONLY when a source is missing required cookie values.
        This loader does NOT perform authentication validation - that happens later
        when Gemini WebAPI initializes or makes authenticated requests.
        """
        # Priority 1: [Gemini] section (canonical provider-scoped format)
        if "Gemini" in CONFIG:
            gemini_config = dict(CONFIG["Gemini"])
            psid_val = gemini_config.get("__Secure-1PSID", "").strip()
            psidts_val = gemini_config.get("__Secure-1PSIDTS", "").strip()

            # Source is usable ONLY when BOTH cookies are present and non-empty
            if psid_val and psidts_val:
                # Clean up quoted values if present
                psid_val = psid_val.strip('"')
                psidts_val = psidts_val.strip('"')

                reconstructed_cookies = [
                    {
                        "name": "__Secure-1PSID",
                        "value": psid_val,
                        "domain": ".google.com",
                        "path": "/"
                    },
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": psidts_val,
                        "domain": ".google.com",
                        "path": "/"
                    }
                ]
                return {"cookies": reconstructed_cookies}, False
            # If either cookie is missing or empty, fall through to next priority

        # Priority 2: Legacy [Cookies] section (backward compatibility)
        if "Cookies" in CONFIG:
            config_cookies = dict(CONFIG["Cookies"])
            # Only support legacy aliases - do NOT support __Secure- prefixed keys here
            psid_val = config_cookies.get("gemini_cookie_1psid", "").strip()
            psidts_val = config_cookies.get("gemini_cookie_1psidts", "").strip()

            # Source is usable ONLY when BOTH cookies are present and non-empty
            if psid_val and psidts_val:
                # Clean up quoted values if present
                psid_val = psid_val.strip('"')
                psidts_val = psidts_val.strip('"')

                _warn_legacy_gemini_cookie_config_once()
                reconstructed_cookies = [
                    {
                        "name": "__Secure-1PSID",
                        "value": psid_val,
                        "domain": ".google.com",
                        "path": "/"
                    },
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": psidts_val,
                        "domain": ".google.com",
                        "path": "/"
                    }
                ]
                return {"cookies": reconstructed_cookies}, True
            # If either cookie is missing or empty, fall through to next priority

        # Priority 3: Canonical store (lowest priority now)
        canonical = cls.load_canonical_state()
        if canonical:
            return canonical, False

        return None, False

    @classmethod
    def get_gemini_config_source(cls) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Get cookies from [Gemini] section in config.conf.
        Returns Tuple (cookies_data_dict, is_legacy_fallback).
        Returns (None, False) if section missing or cookies incomplete.
        """
        if "Gemini" not in CONFIG:
            return None, False

        gemini_config = dict(CONFIG["Gemini"])
        psid_val = gemini_config.get("__Secure-1PSID", "").strip()
        psidts_val = gemini_config.get("__Secure-1PSIDTS", "").strip()

        # Source is usable ONLY when BOTH cookies are present and non-empty
        if psid_val and psidts_val:
            # Clean up quoted values if present
            psid_val = psid_val.strip('"')
            psidts_val = psidts_val.strip('"')

            reconstructed_cookies = [
                {
                    "name": "__Secure-1PSID",
                    "value": psid_val,
                    "domain": ".google.com",
                    "path": "/"
                },
                {
                    "name": "__Secure-1PSIDTS",
                    "value": psidts_val,
                    "domain": ".google.com",
                    "path": "/"
                }
            ]
            return {"cookies": reconstructed_cookies}, False

        return None, False

    @classmethod
    def get_legacy_cookie_source(cls) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Get cookies from legacy [Cookies] section in config.conf.
        Returns Tuple (cookies_data_dict, is_legacy_fallback).
        Returns (None, False) if section missing or cookies incomplete.
        """
        if "Cookies" not in CONFIG:
            return None, False

        config_cookies = dict(CONFIG["Cookies"])
        # Only support legacy aliases - do NOT support __Secure- prefixed keys here
        psid_val = config_cookies.get("gemini_cookie_1psid", "").strip()
        psidts_val = config_cookies.get("gemini_cookie_1psidts", "").strip()

        # Source is usable ONLY when BOTH cookies are present and non-empty
        if psid_val and psidts_val:
            # Clean up quoted values if present
            psid_val = psid_val.strip('"')
            psidts_val = psidts_val.strip('"')

            _warn_legacy_gemini_cookie_config_once()
            reconstructed_cookies = [
                {
                    "name": "__Secure-1PSID",
                    "value": psid_val,
                    "domain": ".google.com",
                    "path": "/"
                },
                {
                    "name": "__Secure-1PSIDTS",
                    "value": psidts_val,
                    "domain": ".google.com",
                    "path": "/"
                }
            ]
            return {"cookies": reconstructed_cookies}, True

        return None, False

    @classmethod
    def get_json_source(cls) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Get cookies from canonical store runtime/auth/gemini.json.
        Returns Tuple (cookies_data_dict, is_legacy_fallback).
        Returns (None, False) if file missing, empty, or invalid.
        """
        canonical = cls.load_canonical_state()
        if canonical:
            return canonical, False
        return None, False

    @classmethod
    def validate_state_structure(cls, data: Any) -> bool:
        """
        Validates the structure of the loaded auth state dictionary.
        Must contain a list of 'cookies' with at least 'name' and 'value'.
        """
        if not isinstance(data, dict):
            return False
        cookies = data.get("cookies")
        if not isinstance(cookies, list):
            return False

        for cookie in cookies:
            if not isinstance(cookie, dict):
                return False
            if "name" not in cookie or "value" not in cookie:
                return False
        return True

    @classmethod
    def translate_to_playwright(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Returns the state exactly as-is for Playwright context initialization,
        ensuring it conforms to Playwright storageState schema.
        """
        return {
            "cookies": data.get("cookies", []),
            "origins": data.get("origins", [])
        }

    @classmethod
    def translate_to_webapi(cls, data: Dict[str, Any]) -> Tuple[Dict[str, str], Optional[str], Optional[str]]:
        """
        Translates the unified cookies list into formats required by gemini-webapi:
        Returns (cookies_dict, secure_1psid, secure_1psidts)
        """
        cookies_list = data.get("cookies", [])
        extracted_cookies = {}
        for cookie in cookies_list:
            if "google.com" in cookie.get("domain", ""):
                name = cookie.get("name")
                if name:
                    extracted_cookies[name] = cookie.get("value")

        psid = extracted_cookies.get("__Secure-1PSID")
        psidts = extracted_cookies.get("__Secure-1PSIDTS")
        return extracted_cookies, psid, psidts
