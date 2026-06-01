from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.browser.engine import BrowserEngine
from app.services.browser.errors import BrowserShuttingDownError


def make_browser():
    browser = MagicMock()
    browser.close = AsyncMock()
    browser.is_connected.return_value = True
    return browser


@pytest.mark.asyncio
async def test_engine_close_sets_terminal_shutdown_and_closes_owned_resources():
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 0
    session.close_resources = AsyncMock()

    engine.browser = browser
    engine.playwright = playwright
    engine.sessions = {"gemini": session}

    await engine.close()

    assert engine.is_shutting_down is True
    assert engine._shutdown_started is True
    session.close_resources.assert_awaited_once_with(save_state=True)
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert engine.sessions == {}
    assert engine.browser is None
    assert engine.playwright is None


@pytest.mark.asyncio
async def test_engine_close_is_idempotent():
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    session = MagicMock()
    session.name = "gemini"
    session.active_lease_count = 0
    session.close_resources = AsyncMock()

    engine.browser = browser
    engine.playwright = playwright
    engine.sessions = {"gemini": session}

    await engine.close()
    await engine.close()

    session.close_resources.assert_awaited_once_with(save_state=True)
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_healthy_browser_noops_after_shutdown(mocker):
    engine = BrowserEngine(headless=True)
    browser = make_browser()
    browser.is_connected.return_value = False
    playwright = MagicMock()
    playwright.stop = AsyncMock()

    engine.browser = browser
    engine.playwright = playwright
    engine.browser_generation = 4
    engine.is_shutting_down = True
    async_playwright = mocker.patch("app.services.browser.engine.async_playwright")

    await engine._ensure_healthy_browser()

    async_playwright.assert_not_called()
    assert engine.browser_generation == 4
    assert engine.browser is browser
    assert engine.playwright is playwright


@pytest.mark.asyncio
async def test_get_page_after_shutdown_fails_fast():
    engine = BrowserEngine(headless=True)
    engine.is_shutting_down = True

    with pytest.raises(BrowserShuttingDownError):
        await engine.get_page("gemini")
