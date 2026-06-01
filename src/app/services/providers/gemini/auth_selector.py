from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from app.services.browser.auth_loader import GeminiAuthStateLoader
from app.logger import logger


@dataclass(frozen=True)
class GeminiAuthCandidate:
    source_name: str
    source_type: str
    auth_data: Dict[str, Any]
    is_legacy: bool
    supports_webapi_cookie_auth: bool
    supports_playwright_storage: bool
    migration_needed: bool


class GeminiAuthSelector:
    """
    Enumerates Gemini auth candidates in provider-defined priority order.

    This selector does not validate account status, create backend clients,
    activate browser contexts, or decide guest-mode fallback.
    """

    _SOURCE_METHODS = (
        ("[Gemini] config", "gemini_config", "get_gemini_config_source"),
        ("[Cookies] legacy config", "legacy_cookies", "get_legacy_cookie_source"),
        ("gemini.json canonical store", "json_store", "get_json_source"),
    )

    @classmethod
    def iter_candidates(cls) -> Iterator[GeminiAuthCandidate]:
        for source_name, source_type, source_method_name in cls._SOURCE_METHODS:
            logger.debug("AuthSelector: Attempting source: %s", source_name)
            source_getter = getattr(GeminiAuthStateLoader, source_method_name)
            auth_data, is_legacy = source_getter()
            if auth_data is None:
                logger.debug("AuthSelector: Source unavailable: %s", source_name)
                continue

            _, psid, _ = GeminiAuthStateLoader.translate_to_webapi(auth_data)
            candidate = GeminiAuthCandidate(
                source_name=source_name,
                source_type=source_type,
                auth_data=auth_data,
                is_legacy=is_legacy,
                supports_webapi_cookie_auth=bool(psid),
                supports_playwright_storage=True,
                migration_needed=is_legacy,
            )
            logger.info(
                "AuthSelector: Source selected: %s",
                source_name,
                extra={
                    "source_name": source_name,
                    "source_type": source_type,
                    "is_legacy": is_legacy,
                    "supports_webapi": bool(psid),
                    "migration_needed": is_legacy
                }
            )
            yield candidate

    @classmethod
    def first_playwright_storage_candidate(cls) -> Optional[GeminiAuthCandidate]:
        candidate = next(
            (
                candidate
                for candidate in cls.iter_candidates()
                if candidate.supports_playwright_storage
            ),
            None,
        )
        if candidate:
            logger.info(
                "AuthSelector: Using candidate for Playwright storage: %s",
                candidate.source_name,
                extra={"source_name": candidate.source_name, "source_type": candidate.source_type}
            )
        else:
            logger.warning("AuthSelector: No Playwright-compatible auth source available")
        return candidate
