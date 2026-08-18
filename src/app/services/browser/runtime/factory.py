from app.config import CONFIG
from app.services.browser.runtime.base import BrowserRuntime
from app.services.browser.runtime.playwright_runtime import PlaywrightChromiumRuntime


def create_browser_runtime(*, headless=None) -> BrowserRuntime:
    runtime_name = CONFIG.get("Browser", "runtime", fallback="playwright").strip().lower()
    if runtime_name == "playwright":
        return PlaywrightChromiumRuntime(headless=headless)
    raise ValueError(
        f"Unsupported browser runtime configured: '{runtime_name}'. Supported values: 'playwright'."
    )
