from typing import Any, Callable, Optional

from playwright.async_api import async_playwright, Playwright, Error as PlaywrightError

from app.logger import logger
from app.services.browser.runtime.base import BrowserRuntime


class PlaywrightChromiumRuntime(BrowserRuntime):
    """Playwright/Chromium launcher. Launch mechanics only; lifecycle authority stays in BrowserEngine."""

    def __init__(self, headless: Optional[bool] = None):
        self.headless = headless
        self._playwright: Optional[Playwright] = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()

    async def launch_browser(self) -> Any:
        return await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )

    def bind_disconnect(self, browser: Any, callback: Callable[[], None]) -> None:
        browser.on("disconnected", lambda b: callback())

    def is_browser_connected(self, browser: Any) -> bool:
        return browser.is_connected()

    async def close_browser(self, browser: Any, phase: str) -> None:
        if not browser:
            return

        try:
            connected = self.is_browser_connected(browser)
        except Exception as inspection_error:
            logger.warning(
                "BrowserRuntime: Failed to inspect browser connection before close: %s",
                inspection_error,
                exc_info=True,
                extra={"phase": phase},
            )
            connected = True

        if not connected:
            logger.debug(
                "BrowserRuntime: Skipping browser close; transport already disconnected.",
                extra={"phase": phase},
            )
            return

        try:
            await browser.close()
        except PlaywrightError as close_error:
            try:
                connected_after_error = self.is_browser_connected(browser)
            except Exception as inspection_error:
                logger.warning(
                    "BrowserRuntime: Browser close failed (%s); post-close connection inspection failed: %s",
                    close_error,
                    inspection_error,
                    exc_info=(type(close_error), close_error, close_error.__traceback__),
                    extra={"phase": phase},
                )
                return

            if connected_after_error:
                logger.warning(
                    "BrowserRuntime: Error closing browser: %s",
                    close_error,
                    exc_info=True,
                    extra={"phase": phase},
                )
            else:
                logger.debug(
                    "BrowserRuntime: Browser transport disconnected during close.",
                    extra={"phase": phase},
                )
        except Exception as close_error:
            # Playwright 1.6x can surface the known driver transport-close
            # race as a plain builtin Exception (transport sets it on
            # IncompleteReadError; wrap_api_call's rewrite_error preserves
            # the generic type), so the PlaywrightError branch above cannot
            # see it. Classify it benign only with full evidence: exact
            # transport signature AND post-error disconnect.
            known_transport_close = (
                "connection closed while reading from the driver"
                in str(close_error).lower()
            )
            if not known_transport_close:
                logger.warning(
                    "BrowserRuntime: Error closing browser: %s",
                    close_error,
                    exc_info=(type(close_error), close_error, close_error.__traceback__),
                    extra={"phase": phase},
                )
                return

            try:
                connected_after_error = self.is_browser_connected(browser)
            except Exception as inspection_error:
                logger.warning(
                    "BrowserRuntime: Browser close failed (%s); post-close connection inspection failed: %s",
                    close_error,
                    inspection_error,
                    exc_info=(type(close_error), close_error, close_error.__traceback__),
                    extra={"phase": phase},
                )
                return

            if connected_after_error:
                logger.warning(
                    "BrowserRuntime: Error closing browser: %s",
                    close_error,
                    exc_info=True,
                    extra={"phase": phase},
                )
            else:
                logger.debug(
                    "BrowserRuntime: Browser transport disconnected during close.",
                    extra={"phase": phase},
                )

    async def stop(self) -> None:
        if not self._playwright:
            return
        playwright = self._playwright
        try:
            await playwright.stop()
        except Exception as e:
            logger.warning(f"BrowserRuntime: Error stopping playwright: {e}", exc_info=True)
        finally:
            self._playwright = None
