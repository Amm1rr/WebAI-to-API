import configparser
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import Error as PlaywrightError

from app.services.browser.runtime.factory import create_browser_runtime
from app.services.browser.runtime.playwright_runtime import PlaywrightChromiumRuntime


def make_config(runtime=None):
    cfg = configparser.ConfigParser()
    cfg["Browser"] = {"name": "chrome"}
    if runtime is not None:
        cfg["Browser"]["runtime"] = runtime
    return cfg


def make_browser():
    browser = MagicMock()
    browser.close = AsyncMock()
    browser.is_connected.return_value = True
    return browser


def make_runtime():
    return PlaywrightChromiumRuntime(headless=True)


def test_default_runtime_resolves_to_playwright(mocker):
    mocker.patch("app.services.browser.runtime.factory.CONFIG", new=make_config("playwright"))
    runtime = create_browser_runtime(headless=True)
    assert isinstance(runtime, PlaywrightChromiumRuntime)
    assert runtime.headless is True


def test_missing_runtime_key_falls_back_to_playwright(mocker):
    mocker.patch("app.services.browser.runtime.factory.CONFIG", new=make_config())
    assert isinstance(create_browser_runtime(headless=True), PlaywrightChromiumRuntime)


def test_unsupported_runtime_fails_explicitly(mocker):
    mocker.patch("app.services.browser.runtime.factory.CONFIG", new=make_config("firefox"))
    with pytest.raises(ValueError, match="firefox"):
        create_browser_runtime(headless=True)


@pytest.mark.asyncio
async def test_start_delegates_to_async_playwright(mocker):
    playwright = MagicMock()
    playwright.start = AsyncMock(return_value=playwright)
    mocker.patch(
        "app.services.browser.runtime.playwright_runtime.async_playwright",
        return_value=playwright,
    )
    runtime = make_runtime()

    await runtime.start()

    playwright.start.assert_awaited_once_with()
    assert runtime._playwright is playwright


@pytest.mark.asyncio
async def test_launch_uses_chromium_with_headless_config(mocker):
    browser = MagicMock()
    playwright = MagicMock()
    playwright.start = AsyncMock(return_value=playwright)
    playwright.chromium.launch = AsyncMock(return_value=browser)
    mocker.patch(
        "app.services.browser.runtime.playwright_runtime.async_playwright",
        return_value=playwright,
    )
    runtime = make_runtime()

    await runtime.start()
    launched = await runtime.launch_browser()

    assert launched is browser
    playwright.chromium.launch.assert_awaited_once_with(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )


def test_bind_disconnect_installs_callback():
    runtime = make_runtime()
    browser = MagicMock()
    callback = MagicMock()

    runtime.bind_disconnect(browser, callback)

    browser.on.assert_called_once()
    handler = browser.on.call_args.args[1]
    handler(None)
    callback.assert_called_once()


def test_is_browser_connected_delegates():
    runtime = make_runtime()
    browser = MagicMock()
    browser.is_connected.return_value = False

    assert runtime.is_browser_connected(browser) is False


@pytest.mark.asyncio
async def test_close_browser_noop_without_browser():
    runtime = make_runtime()
    await runtime.close_browser(None, "terminal")


@pytest.mark.asyncio
async def test_close_skips_disconnected_browser(caplog):
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.return_value = False

    await runtime.close_browser(browser, "terminal")

    browser.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_transport_race_is_benign_after_disconnect(caplog):
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.side_effect = [True, False]
    browser.close.side_effect = PlaywrightError("transport closed")

    await runtime.close_browser(browser, "terminal")

    browser.close.assert_awaited_once()
    assert not any("Error closing browser" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_close_error_while_connected_remains_warning(caplog):
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.return_value = True
    browser.close.side_effect = PlaywrightError("close failed")

    await runtime.close_browser(browser, "terminal")

    assert any("Error closing browser" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_generic_close_error_warns_after_disconnect(caplog):
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.side_effect = [True, False]
    browser.close.side_effect = RuntimeError("programming failure")

    await runtime.close_browser(browser, "terminal")

    assert any("Error closing browser" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_generic_transport_close_race_is_benign(caplog):
    """Exact observed shape: Playwright 1.6x raises the driver transport
    race as a plain builtin Exception; with post-error disconnect it is a
    benign close race, not a warning."""
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.side_effect = [True, False]
    browser.close.side_effect = Exception(
        "Browser.close: Connection closed while reading from the driver"
    )

    await runtime.close_browser(browser, "terminal")

    browser.close.assert_awaited_once()
    assert not any(
        "Error closing browser" in record.message for record in caplog.records
    )
    assert any(
        "Browser transport disconnected during close." in record.message
        for record in caplog.records
        if record.levelname == "DEBUG"
    )


@pytest.mark.asyncio
async def test_generic_transport_close_inspection_failure_warns(caplog):
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.side_effect = [True, RuntimeError("inspection failed")]
    browser.close.side_effect = Exception(
        "Browser.close: Connection closed while reading from the driver"
    )

    await runtime.close_browser(browser, "terminal")

    browser.close.assert_awaited_once()
    messages = [record.message for record in caplog.records]
    warning_messages = [
        record.message for record in caplog.records if record.levelname == "WARNING"
    ]
    assert warning_messages
    assert any("Connection closed while reading from the driver" in message for message in messages)
    assert any("inspection failed" in message for message in messages)


@pytest.mark.asyncio
async def test_generic_transport_signature_while_connected_remains_warning(caplog):
    """Signature match alone is insufficient: still-connected stays WARNING."""
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.return_value = True  # before and after
    browser.close.side_effect = Exception(
        "Browser.close: Connection closed while reading from the driver"
    )

    await runtime.close_browser(browser, "terminal")

    assert any("Error closing browser" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_generic_close_error_warns_while_connected(caplog):
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.return_value = True
    browser.close.side_effect = RuntimeError("programming failure")

    await runtime.close_browser(browser, "terminal")

    assert any("Error closing browser" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_close_inspection_failure_preserves_original_error(caplog):
    runtime = make_runtime()
    browser = make_browser()
    browser.is_connected.side_effect = [True, RuntimeError("inspection failed")]
    browser.close.side_effect = PlaywrightError("close transport failure")

    await runtime.close_browser(browser, "terminal")

    messages = [record.message for record in caplog.records]
    assert any("close transport failure" in message for message in messages)
    assert any("inspection failed" in message for message in messages)


@pytest.mark.asyncio
async def test_stop_noops_when_never_started():
    runtime = make_runtime()
    await runtime.stop()


@pytest.mark.asyncio
async def test_stop_calls_playwright_stop_and_resets():
    runtime = make_runtime()
    playwright = MagicMock()
    playwright.stop = AsyncMock()
    runtime._playwright = playwright

    await runtime.stop()

    playwright.stop.assert_awaited_once_with()
    assert runtime._playwright is None


@pytest.mark.asyncio
async def test_stop_swallows_playwright_error():
    runtime = make_runtime()
    playwright = MagicMock()
    playwright.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    runtime._playwright = playwright

    await runtime.stop()

    assert runtime._playwright is None
