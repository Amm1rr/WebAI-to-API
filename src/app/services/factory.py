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

    # Backward compatibility aliases
    _ALIASES = {
        "playwright": "gemini",
    }

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
            elif provider_name in cls._ALIASES:
                provider_key = cls._ALIASES[provider_name]
        
        # 2. Check model prefix (e.g., "atlas/model-name", "playwright/gemini-pro")
        elif "/" in model_name:
            prefix, actual_model = model_name.split("/", 1)
            prefix = prefix.lower().strip()
            if prefix in cls._registry:
                provider_key = prefix
                model_name = actual_model.strip()
            elif prefix in cls._ALIASES:
                provider_key = cls._ALIASES[prefix]
                # Note: We keep the original model_name (including prefix) 
                # so the GeminiProvider can interpret it as a directive 
                # to use the Playwright adapter.

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
