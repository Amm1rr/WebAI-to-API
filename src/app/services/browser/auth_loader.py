# src/app/services/browser/auth_loader.py
import os
import json
from typing import Dict, List, Any, Optional, Tuple
from app.config import CONFIG, get_default_auth_state_dir
from app.logger import logger

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
        Priority 1: Load from canonical store runtime/auth/gemini.json.
        Priority 2: Load read-only from legacy [Cookies] in config.conf (logs deprecation).
        
        Returns Tuple (cookies_data_dict, is_legacy_fallback)
        """
        # Priority 1: Canonical store
        canonical = cls.load_canonical_state()
        if canonical:
            return canonical, False

        # Priority 2: Legacy fallback
        config_cookies = dict(CONFIG["Cookies"]) if "Cookies" in CONFIG else {}
        psid_val = config_cookies.get("__Secure-1PSID") or config_cookies.get("gemini_cookie_1psid")
        psidts_val = config_cookies.get("__Secure-1PSIDTS") or config_cookies.get("gemini_cookie_1psidts")
        
        if psid_val:
            psid_val = psid_val.strip('"')
            psidts_val = psidts_val.strip('"') if psidts_val else None
            
            logger.warning(
                "AuthManager: Loaded deprecated cookies from config.conf. "
                "Please migrate your authentication state to runtime/auth/gemini.json. "
                "Support for configuration-based cookie storage will be removed in a future release."
            )
            # Reconstruct into canonical-like state dictionary format
            reconstructed_cookies = [
                {
                    "name": "__Secure-1PSID",
                    "value": psid_val,
                    "domain": ".google.com",
                    "path": "/"
                }
            ]
            if psidts_val:
                reconstructed_cookies.append({
                    "name": "__Secure-1PSIDTS",
                    "value": psidts_val,
                    "domain": ".google.com",
                    "path": "/"
                })
            return {"cookies": reconstructed_cookies}, True

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
