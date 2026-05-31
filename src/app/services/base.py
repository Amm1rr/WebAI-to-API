from abc import ABC, abstractmethod
from typing import Any, List, Optional
from app.schemas.request import OpenAIChatRequest
from app.services.providers.base_repository import ProviderCapability

class BaseProvider(ABC):
    """
    Abstract base class for all AI providers.
    Defines the lightweight contract for external behavior normalization.
    """
    capabilities: set[ProviderCapability] = set()

    @abstractmethod
    async def chat_completions(self, request: OpenAIChatRequest) -> Any:
        """
        Handle a chat completion request and return an OpenAI-compatible response.
        This can return either a dictionary (for non-streaming) or a StreamingResponse.
        """
        pass

    @abstractmethod
    async def list_models(self) -> List[dict]:
        """
        Return a list of supported models for this provider in OpenAI format.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close any underlying resources or clients.
        """
        pass
