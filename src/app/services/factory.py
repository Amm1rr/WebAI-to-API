from typing import Optional
from app.services.base import BaseProvider
from app.services.providers.gemini import GeminiProvider
from app.services.providers.atlas import AtlasProvider
from schemas.request import OpenAIChatRequest

class ProviderFactory:
    """
    Static registry for resolving providers.
    Lazily initializes provider instances and routes requests based on model names.
    """
    
    _instances = {}
    _registry = {
        "gemini": GeminiProvider,
        "atlas": AtlasProvider,
    }

    @classmethod
    def get_provider(cls, request: OpenAIChatRequest) -> tuple[BaseProvider, str]:
        """
        Resolve a provider based on the request.
        Returns the provider instance and the resolved model name.
        """
        model_name = request.model or ""
        provider_key = "gemini" # Default
        
        # 1. Check explicit provider field
        if request.provider:
            provider_name = request.provider.lower().strip()
            if provider_name in cls._registry:
                provider_key = provider_name
            else:
                model_name = request.model # Keep original if provider not found
        # 2. Check model prefix (e.g., "atlas/model-name")
        elif "/" in model_name:
            prefix, actual_model = model_name.split("/", 1)
            prefix = prefix.lower().strip()
            if prefix in cls._registry:
                provider_key = prefix
                model_name = actual_model.strip()

        if provider_key not in cls._instances:
            cls._instances[provider_key] = cls._registry[provider_key]()

        return cls._instances[provider_key], model_name

    @classmethod
    async def close_all(cls):
        """Close all registered providers."""
        for provider in cls._instances.values():
            await provider.close()
        cls._instances.clear()
