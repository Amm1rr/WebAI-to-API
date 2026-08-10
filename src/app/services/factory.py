from typing import Optional
from app.services.base import BaseProvider
from app.services.providers.gemini.provider import GeminiProvider
from app.services.providers.atlas import AtlasProvider
from app.schemas.request import OpenAIChatRequest

class ProviderFactory:
    """
    Static registry for resolving logical providers.
    Lazily initializes provider instances and routes requests based on provider identity.
    """
    
    _instances = {}
    _registry = {
        "gemini": GeminiProvider,
        "atlas": AtlasProvider,
    }
    _browser_registry = {
        "gemini": GeminiProvider,
    }
    _default_browser_provider = "gemini"

    # Backward compatibility aliases
    _ALIASES = {
        "playwright": _default_browser_provider,
    }

    @classmethod
    def _resolve_browser_provider(cls, browser_provider_name: Optional[str]) -> str:
        if browser_provider_name and browser_provider_name in cls._browser_registry:
            return browser_provider_name
        return cls._default_browser_provider

    @classmethod
    def _resolve_legacy_playwright_model(cls, model_name: str) -> tuple[str, str]:
        """
        Normalize the browser-native model namespace.

        Supported forms:
        - legacy: playwright/<model>
        - provider-aware: playwright/<provider>/<model>
        """
        browser_provider_name = cls._default_browser_provider
        normalized_model = f"playwright/{model_name.strip()}"

        if "/" in model_name:
            candidate_provider, actual_model = model_name.split("/", 1)
            candidate_provider = candidate_provider.lower().strip()
            if candidate_provider in cls._browser_registry:
                browser_provider_name = candidate_provider
                normalized_model = f"playwright/{candidate_provider}/{actual_model.strip()}"

        return browser_provider_name, normalized_model

    @classmethod
    def register_provider(cls, provider_name: str, provider_cls: type[BaseProvider], *, browser_native: bool = False) -> None:
        cls._registry[provider_name] = provider_cls
        if browser_native:
            cls._browser_registry[provider_name] = provider_cls

    @classmethod
    def register_browser_provider(cls, provider_name: str, provider_cls: type[BaseProvider]) -> None:
        cls.register_provider(provider_name, provider_cls, browser_native=True)

    @classmethod
    def get_provider(cls, request: OpenAIChatRequest) -> tuple[BaseProvider, str]:
        """
        Resolve a logical provider based on the request.
        Returns the provider instance and the resolved model name.
        """
        model_name = request.model or ""
        provider_key = "gemini" # Default
        
        # 1. Check explicit provider field
        if request.provider:
            provider_name = request.provider.lower().strip()
            if provider_name in cls._registry:
                provider_key = provider_name
            elif provider_name in cls._browser_registry:
                provider_key = provider_name
            elif provider_name in cls._ALIASES:
                provider_key = cls._ALIASES[provider_name]
        
        # 2. Check model prefix (e.g., "atlas/model-name", "playwright/gemini-pro")
        elif "/" in model_name:
            prefix, actual_model = model_name.split("/", 1)
            prefix = prefix.lower().strip()
            if prefix in cls._registry:
                provider_key = prefix
                model_name = actual_model.strip()
            elif prefix in cls._browser_registry:
                provider_key = prefix
                model_name = f"playwright/{prefix}/{actual_model.strip()}"
            elif prefix in cls._ALIASES:
                browser_provider, normalized_model = cls._resolve_legacy_playwright_model(actual_model.strip())
                provider_key = browser_provider
                model_name = normalized_model
            elif prefix == "playwright":
                browser_provider, normalized_model = cls._resolve_legacy_playwright_model(actual_model.strip())
                provider_key = browser_provider
                model_name = normalized_model

        if provider_key not in cls._instances:
            cls._instances[provider_key] = cls._registry[provider_key]()

        return cls._instances[provider_key], model_name

    @classmethod
    async def close_provider(cls, provider_key: str):
        """Close and clear a specific registered provider."""
        provider = cls._instances.pop(provider_key, None)
        if provider:
            try:
                await provider.close()
            except Exception as e:
                from app.logger import logger
                logger.warning(f"Error closing provider '{provider_key}': {e}")

    @classmethod
    async def close_all(cls):
        """Close all registered providers."""
        for provider in cls._instances.values():
            await provider.close()
        cls._instances.clear()
