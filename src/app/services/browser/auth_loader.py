# src/app/services/browser/auth_loader.py
import os
import json
import math
import time
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
        Deprecated compatibility helper.

        This function historically combined discovery and source-selection logic.
        Primary runtime selection now lives in GeminiAuthSelector.

        New code should use:

            GeminiAuthSelector.iter_candidates()

        or:

            GeminiAuthSelector.first_playwright_storage_candidate()

        depending on use case.

        Retained only for backward compatibility.

        Loads authentication cookies utilizing the prioritized hierarchy.
        Priority 1: Load from [Gemini] section in config.conf (canonical format).
        Priority 2: Load from legacy [Cookies] in config.conf (logs deprecation).
        Priority 3: Load from canonical store runtime/auth/gemini.json.

        Returns Tuple (cookies_data_dict, is_legacy_fallback)

        Production Gemini auth source selection is owned by GeminiAuthSelector.
        Keep this method behavior unchanged until downstream compatibility risk is
        intentionally retired.

        Note: Fallback occurs ONLY when a source is missing required cookie values.
        This loader does NOT perform authentication validation - that happens later
        when Gemini WebAPI initializes or makes authenticated requests.
        """
        # NOTE:
        # Retained for compatibility and legacy tests.
        #
        # Do not add new production call sites.
        #
        # Future removal requires:
        #     - verification of external consumers
        #     - migration of remaining compatibility tests
        # Priority 1: [Gemini] section (canonical provider-scoped format)
        data, is_legacy = cls.get_gemini_config_source()
        if data:
            return data, is_legacy

        # Priority 2: Legacy [Cookies] section (backward compatibility)
        data, is_legacy = cls.get_legacy_cookie_source()
        if data:
            return data, is_legacy

        # Priority 3: Canonical store runtime/auth/gemini.json (lowest priority)
        data, is_legacy = cls.get_json_source()
        if data:
            return data, is_legacy

        return None, False

    @classmethod
    def _normalize_config_value(cls, value: Optional[str]) -> Optional[str]:
        """
        Normalizes a configuration value by stripping whitespace and double quotes.
        Returns None if the result is an empty string.
        """
        if value is None:
            return None
        
        normalized = value.strip().strip('"').strip()
        return normalized if normalized else None

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
        # Support both canonical and common alias names in the [Gemini] section
        psid_val = (
            cls._normalize_config_value(gemini_config.get("__Secure-1PSID")) or
            cls._normalize_config_value(gemini_config.get("gemini_cookie_1psid")) or
            cls._normalize_config_value(gemini_config.get("gemini_cookie_1PSID"))
        )
        psidts_val = (
            cls._normalize_config_value(gemini_config.get("__Secure-1PSIDTS")) or
            cls._normalize_config_value(gemini_config.get("gemini_cookie_1psidts")) or
            cls._normalize_config_value(gemini_config.get("gemini_cookie_1PSIDTS"))
        )

        # PSID is required; PSIDTS is optional.
        if psid_val:
            reconstructed_cookies = [
                {
                    "name": "__Secure-1PSID",
                    "value": psid_val,
                    "domain": ".google.com",
                    "path": "/"
                }
            ]
            if psidts_val:
                reconstructed_cookies.append(
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": psidts_val,
                        "domain": ".google.com",
                        "path": "/"
                    }
                )
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
        # Support all legacy and standard key variants for backward compatibility
        # Select the first normalized non-empty candidate
        psid_val = (
            cls._normalize_config_value(config_cookies.get("gemini_cookie_1psid")) or 
            cls._normalize_config_value(config_cookies.get("gemini_cookie_1PSID")) or 
            cls._normalize_config_value(config_cookies.get("__Secure-1PSID"))
        )
        
        psidts_val = (
            cls._normalize_config_value(config_cookies.get("gemini_cookie_1psidts")) or 
            cls._normalize_config_value(config_cookies.get("gemini_cookie_1PSIDTS")) or 
            cls._normalize_config_value(config_cookies.get("__Secure-1PSIDTS"))
        )

        # PSID is required; PSIDTS is optional.
        if psid_val:
            _warn_legacy_gemini_cookie_config_once()
            reconstructed_cookies = [
                {
                    "name": "__Secure-1PSID",
                    "value": psid_val,
                    "domain": ".google.com",
                    "path": "/"
                }
            ]
            if psidts_val:
                reconstructed_cookies.append(
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": psidts_val,
                        "domain": ".google.com",
                        "path": "/"
                    }
                )
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
    def _is_google_domain(cls, domain: Any) -> bool:
        normalized = str(domain or "").lower().lstrip(".")
        return normalized == "google.com" or normalized.endswith(".google.com")

    @classmethod
    def _is_webapi_cookie_candidate(cls, cookie: Any, now: float) -> bool:
        if not isinstance(cookie, dict):
            return False
        if not cookie.get("name") or not cookie.get("value"):
            return False
        if not cls._is_google_domain(cookie.get("domain")):
            return False
        if cookie.get("partitionKey"):
            return False

        expires = cookie.get("expires")
        return not (
            isinstance(expires, (int, float))
            and math.isfinite(expires)
            and expires > 0
            and expires <= now
        )

    @classmethod
    def _cookie_selection_key(cls, cookie: Dict[str, Any], index: int) -> Tuple[int, str, bool, str, int]:
        domain = str(cookie.get("domain", "")).lower()
        normalized_domain = domain.lstrip(".")
        domain_rank = {
            ".google.com": 0,
            "google.com": 1,
            "gemini.google.com": 2,
            "accounts.google.com": 3,
        }.get(domain, 4)
        return (
            domain_rank,
            normalized_domain,
            str(cookie.get("path", "/")) != "/",
            str(cookie.get("path", "/")),
            index,
        )

    @classmethod
    def _select_webapi_cookie(
        cls, cookies: Any, name: str, now: float
    ) -> Optional[Dict[str, Any]]:
        candidates = (
            (cls._cookie_selection_key(cookie, index), cookie)
            for index, cookie in enumerate(cookies)
            if isinstance(cookie, dict)
            and cookie.get("name") == name
            and cls._is_webapi_cookie_candidate(cookie, now)
        )
        selected = min(candidates, default=None, key=lambda candidate: candidate[0])
        return selected[1] if selected else None

    @classmethod
    def _select_google_cookies(cls, cookies: Any, now: float) -> Dict[str, Dict[str, Any]]:
        selected = {}
        for index, cookie in enumerate(cookies):
            if not cls._is_webapi_cookie_candidate(cookie, now):
                continue
            name = cookie["name"]
            candidate_key = cls._cookie_selection_key(cookie, index)
            existing = selected.get(name)
            if existing is None or candidate_key < existing[0]:
                selected[name] = (candidate_key, cookie)
        return {name: candidate[1] for name, candidate in selected.items()}

    @classmethod
    def get_webapi_cookie_material(
        cls, data: Dict[str, Any], *, now: Optional[float] = None
    ) -> Tuple[Dict[str, str], Optional[str], Optional[str]]:
        """Return deterministic, directly usable Gemini WebAPI cookie material."""
        cookies = data.get("cookies", []) if isinstance(data, dict) else []
        now = time.time() if now is None else now
        selected = cls._select_google_cookies(cookies, now)
        extracted = {name: cookie["value"] for name, cookie in selected.items()}
        return (
            extracted,
            extracted.get("__Secure-1PSID"),
            extracted.get("__Secure-1PSIDTS"),
        )

    @classmethod
    def get_browser_webapi_cookie_material(
        cls, cookies: Any, *, now: Optional[float] = None
    ) -> Optional[Dict[str, str]]:
        """Select directly usable Gemini auth cookies from browser cookie data."""
        now = time.time() if now is None else now
        psid = cls._select_webapi_cookie(cookies, "__Secure-1PSID", now)
        if psid is None:
            return None

        material = {"__Secure-1PSID": psid["value"]}
        psidts = cls._select_webapi_cookie(cookies, "__Secure-1PSIDTS", now)
        if psidts is not None:
            material["__Secure-1PSIDTS"] = psidts["value"]
        return material

    @classmethod
    def has_shared_webapi_material(
        cls, data: Dict[str, Any], context_cookies: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        cookies = data.get("cookies", []) if isinstance(data, dict) else []
        now = time.time()
        psid = cls._select_webapi_cookie(cookies, "__Secure-1PSID", now)
        if psid is None:
            return False
        if context_cookies is None:
            return True

        return any(
            cls._is_webapi_cookie_candidate(cookie, now)
            and cookie.get("name") == "__Secure-1PSID"
            and cookie.get("value") == psid.get("value")
            and str(cookie.get("domain", "")).lower() == str(psid.get("domain", "")).lower()
            and str(cookie.get("path", "/")) == str(psid.get("path", "/"))
            and cookie.get("expires") == psid.get("expires")
            for cookie in context_cookies
        )

    @classmethod
    def translate_to_webapi(cls, data: Dict[str, Any]) -> Tuple[Dict[str, str], Optional[str], Optional[str]]:
        """Translate persisted state using the shared Gemini cookie contract."""
        return cls.get_webapi_cookie_material(data)
