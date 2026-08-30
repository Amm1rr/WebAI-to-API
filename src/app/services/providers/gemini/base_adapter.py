from abc import ABC, abstractmethod
from typing import Any, List, Optional
from app.schemas.request import OpenAIChatRequest
from app.services.openai_compatibility import (
    OpenAICompatibilityCapabilities,
    OpenAIRequestCapability,
)


def _gemini_compatibility(backend_name: str) -> OpenAICompatibilityCapabilities:
    return OpenAICompatibilityCapabilities(
        backend_name=backend_name,
        fields={
            "max_tokens": OpenAIRequestCapability.ACCEPTED_NO_EFFECT,
            "max_completion_tokens": OpenAIRequestCapability.ACCEPTED_NO_EFFECT,
            "reasoning_effort": OpenAIRequestCapability.ACCEPTED_NO_EFFECT,
            "stream_options": OpenAIRequestCapability.ACCEPTED_NO_EFFECT,
        },
    )


GEMINI_WEBAPI_OPENAI_COMPATIBILITY = _gemini_compatibility("Gemini WebAPI")
GEMINI_PLAYWRIGHT_OPENAI_COMPATIBILITY = _gemini_compatibility("Gemini Playwright")

class GeminiBackendAdapter(ABC):
    """
    Abstract base class for Gemini backend execution strategies.
    Encapsulates technical details of either WebAPI or Playwright execution.
    """

    openai_compatibility = GEMINI_WEBAPI_OPENAI_COMPATIBILITY

    @abstractmethod
    async def chat_completions(self, request: OpenAIChatRequest, cid: str, is_new_conversation: bool, tools_prompt: str) -> Any:
        """Execute a chat completion request using the specific backend."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close any backend-specific resources."""
        pass
