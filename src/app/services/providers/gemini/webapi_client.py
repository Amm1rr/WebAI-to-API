import logging
from typing import Optional, List, Union, Any
from pathlib import Path
from gemini_webapi import GeminiClient as WebGeminiClient
from app.config import CONFIG

logger = logging.getLogger("app")

# Maps user-facing short names to the internal model identifiers accepted by gemini-webapi.
MODEL_ALIASES = {
    "flash":    "gemini-3-flash",
    "thinking": "gemini-3-flash-thinking",
    "pro":      "gemini-3-pro",
}

def resolve_model_name(model: str) -> str:
    """Resolve a model name alias to its internal identifier."""
    return MODEL_ALIASES.get(model, model)

class MyGeminiClient:
    """
    Wrapper for the Gemini Web API client.
    """
    def __init__(self, secure_1psid: str | None = None, secure_1psidts: str | None = None, proxy: str | None = None, cookies: Any | None = None) -> None:
        self.client = WebGeminiClient(secure_1psid, secure_1psidts, proxy)
        if cookies:
            self.client.cookies = cookies
        self._gems_cache = None

    async def init(self, **kwargs) -> None:
        """Initialize the Gemini client."""
        await self.client.init(**kwargs)

    async def _persist_cookies(self) -> None:
        """
        No-op under unified-auth-state architecture.
        Runtime configuration cookie updates are strictly prohibited.
        """
        pass

    async def generate_content(
        self,
        message: str,
        model: str,
        files: Optional[List[Union[str, Path]]] = None,
        gem: Optional[str] = None,
        temporary: bool = False,
    ):
        """
        Generate content using the Gemini client.
        """
        resolved_model = resolve_model_name(model)
        resolved_gem = await self._resolve_gem(gem) if gem else None
        return await self.client.generate_content(
            message,
            model=resolved_model,
            files=files,
            gem=resolved_gem,
            temporary=temporary,
        )

    async def generate_content_stream(
        self,
        message: str,
        model: str,
        files: Optional[List[Union[str, Path]]] = None,
        gem: Optional[str] = None,
        temporary: bool = False,
    ):
        """
        Generate content streaming using the Gemini client.
        """
        resolved_model = resolve_model_name(model)
        resolved_gem = await self._resolve_gem(gem) if gem else None
        return self.client.generate_content_stream(
            message,
            model=resolved_model,
            files=files,
            gem=resolved_gem,
            temporary=temporary,
        )

    async def fetch_gems(self):
        """Fetch available gems and cache them."""
        # Only attempt to fetch gems if authenticated, as it usually fails in guest mode
        if hasattr(self.client, 'account_status') and self.client.account_status.name != "AVAILABLE":
            logger.warning("Skipping fetch_gems: Client is unauthenticated.")
            self._gems_cache = []
            return []
            
        try:
            self._gems_cache = await self.client.fetch_gems()
            return self._gems_cache
        except Exception as e:
            logger.warning(f"Failed to fetch gems: {e}. Gem resolution by name will be disabled.")
            self._gems_cache = []
            return []

    async def _resolve_gem(self, gem_id_or_name: str):
        """Resolve a gem by ID or name."""
        if self._gems_cache is None:
            await self.fetch_gems()
        
        if not self._gems_cache:
            return gem_id_or_name
            
        for gem in self._gems_cache:
            if hasattr(gem, 'id') and hasattr(gem, 'name'):
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
        resolved_model = resolve_model_name(model)
        # Note: Gem resolution might need to be async if we want to support name resolution here
        # For now, we'll assume gem is passed as ID or already resolved if possible
        # but the underlying library might expect a Gem object.
        session = self.client.start_chat(model=resolved_model, gem=gem)
        
        # Prevent the global DEFAULT_METADATA list from being mutated by copying it.
        from gemini_webapi.constants import DEFAULT_METADATA
        session._ChatSession__metadata = list(DEFAULT_METADATA)
        
        return session
