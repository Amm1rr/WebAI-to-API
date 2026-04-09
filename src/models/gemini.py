import configparser
import logging
import os
from typing import Optional, List, Union
from pathlib import Path
from gemini_webapi import GeminiClient as WebGeminiClient
from app.config import CONFIG

logger = logging.getLogger("app")


class MyGeminiClient:
    """
    Wrapper for the Gemini Web API client.
    """
    def __init__(self, secure_1psid: str, secure_1psidts: str, proxy: str | None = None) -> None:
        self.client = WebGeminiClient(secure_1psid, secure_1psidts, proxy)
        self._gems_cache = None

    async def init(self) -> None:
        """Initialise the Gemini client and persist any rotated cookies."""
        await self.client.init()
        await self._persist_cookies()

    async def _persist_cookies(self) -> None:
        """Persist rotated cookies back to config.conf to survive restarts."""
        config_path = "config.conf"
        if not os.path.exists(config_path):
            return
        try:
            cookies = self.client.cookies
            psid = cookies.get("__Secure-1PSID")
            psidts = cookies.get("__Secure-1PSIDTS")
            if not psid:
                return
            cfg = configparser.ConfigParser()
            cfg.read(config_path, encoding="utf-8")
            if "Cookies" not in cfg:
                cfg["Cookies"] = {}
            cfg["Cookies"]["gemini_cookie_1psid"] = psid
            if psidts:
                cfg["Cookies"]["gemini_cookie_1psidts"] = psidts
            with open(config_path, "w", encoding="utf-8") as f:
                cfg.write(f)
            logger.info("Cookies persisted to config.conf after rotation.")
        except Exception as e:
            logger.warning(f"Failed to persist cookies: {e}")

    async def generate_content(
        self,
        message: str,
        model: str,
        files: Optional[List[Union[str, Path]]] = None,
        gem: Optional[str] = None,
    ):
        """
        Generate content using the Gemini client.
        """
        resolved_gem = await self._resolve_gem(gem) if gem else None
        return await self.client.generate_content(message, model=model, files=files, gem=resolved_gem)

    async def fetch_gems(self):
        """Fetch available gems and cache them."""
        self._gems_cache = await self.client.fetch_gems()
        return self._gems_cache

    async def _resolve_gem(self, gem_id_or_name: str):
        """Resolve a gem by ID or name."""
        if self._gems_cache is None:
            await self.fetch_gems()
        for gem in self._gems_cache:
            if gem.id == gem_id_or_name or gem.name.lower() == gem_id_or_name.lower():
                return gem
        return gem_id_or_name

    async def close(self) -> None:
        """Close the Gemini client."""
        await self.client.close()

    def start_chat(self, model: str, gem: Optional[str] = None):
        """
        Start a chat session with the given model.
        """
        return self.client.start_chat(model=model)
