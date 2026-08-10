from abc import ABC, abstractmethod
from typing import Optional, Any
from playwright.async_api import Page

class BaseProviderAdapter(ABC):
    """
    Minimal, non-behavioral provider adapter interface.
    Abstracts only vendor-specific authentication checks, URL state parsers,
    and prompt DOM submission sequences.
    
    Streaming pipeline, concurrency locks, and browser orchestration remain 
    strictly owned by the orchestrator and session layers.
    """
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Returns the vendor name, e.g. 'gemini'."""
        pass

    @abstractmethod
    async def check_authentication(self, page: Page) -> bool:
        """Checks browser context session credentials."""
        pass

    @abstractmethod
    def extract_conversation_id(self, url: str) -> Optional[str]:
        """Extracts the stateful thread ID from the current browser URL."""
        pass

    @abstractmethod
    async def submit_prompt(self, page: Page, prompt: str, state: Optional[Any] = None) -> bool:
        """Injects, types, and sends the prompt text on the browser DOM."""
        pass
