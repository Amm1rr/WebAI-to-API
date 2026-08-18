from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


class BrowserRuntime(ABC):
    """Launch mechanics for a concrete browser engine.

    Mechanics only. Lifecycle authority (generation, shutdown, session
    coordination) stays with BrowserEngine; a runtime never initiates
    recovery or shutdown.
    """

    @abstractmethod
    async def start(self) -> None:
        """Initialize the underlying browser driver (e.g. async_playwright().start())."""

    @abstractmethod
    async def launch_browser(self) -> Any:
        """Launch a fresh browser instance and return its handle."""

    @abstractmethod
    def bind_disconnect(self, browser: Any, callback: Callable[[], None]) -> None:
        """Register a callback for unexpected browser disconnect."""

    @abstractmethod
    def is_browser_connected(self, browser: Any) -> bool:
        """Report whether the browser transport is still connected."""

    @abstractmethod
    async def close_browser(self, browser: Any, phase: str) -> None:
        """Best-effort close of a browser instance. Must never raise."""

    @abstractmethod
    async def stop(self) -> None:
        """Tear down the driver (e.g. playwright.stop()). Best-effort, idempotent."""
