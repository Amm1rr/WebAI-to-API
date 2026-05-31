from abc import ABC, abstractmethod
from typing import Any, List, Optional
from app.schemas.request import OpenAIChatRequest

class GeminiBackendAdapter(ABC):
    """
    Abstract base class for Gemini backend execution strategies.
    Encapsulates technical details of either WebAPI or Playwright execution.
    """

    @abstractmethod
    async def chat_completions(self, request: OpenAIChatRequest, cid: str, is_new_conversation: bool, tools_prompt: str) -> Any:
        """Execute a chat completion request using the specific backend."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close any backend-specific resources."""
        pass
