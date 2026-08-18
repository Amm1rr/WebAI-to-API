import pytest
import pytest_asyncio
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from app.services.providers.gemini.scripts.gemini_scripts import (
    STOP_OBSERVER_SCRIPT,
    STREAM_EXTRACTOR_SCRIPT,
)


@pytest_asyncio.fixture
async def observer_page():
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            pytest.skip(f"Chromium unavailable: {error}")

        context = await browser.new_context()
        page = await context.new_page()
        events = []
        await page.expose_function("__test_emit", lambda payload: events.append(payload))
        await page.set_content('<main id="app"></main>')
        await page.evaluate(f"({STREAM_EXTRACTOR_SCRIPT})('__test_emit', 'test-request')")

        yield page, events

        await page.evaluate(f"({STOP_OBSERVER_SCRIPT})('test-request')")
        await context.close()
        await browser.close()


async def add_response(page, text="answer"):
    await page.evaluate(
        """
        () => {
            const response = document.createElement("message-content");
            document.querySelector("#app").append(response);
        }
        """
    )
    await page.wait_for_timeout(150)
    await page.evaluate(
        """
        (value) => {
            document.querySelector("message-content").textContent = value;
        }
        """,
        text,
    )
    await page.wait_for_timeout(150)


@pytest.mark.asyncio
async def test_stop_disappears_after_generation_and_emits_done(observer_page):
    page, events = observer_page
    await page.evaluate(
        """
        () => document.querySelector("#app").innerHTML = `
            <button class="stop-button">active</button>
        `
        """
    )

    await add_response(page)
    await page.evaluate('document.querySelector(".stop-button").style.display = "none"')
    await page.wait_for_timeout(150)

    assert [event["type"] for event in events].count("done") == 1


@pytest.mark.asyncio
async def test_no_active_generation_signal_does_not_emit_done(observer_page):
    page, events = observer_page

    await add_response(page)
    await page.wait_for_timeout(150)

    assert [event["type"] for event in events].count("done") == 0


@pytest.mark.asyncio
async def test_visible_stop_prevents_completion(observer_page):
    page, events = observer_page
    await page.evaluate(
        """
        () => document.querySelector("#app").innerHTML = `
            <button class="stop-button">active</button>
        `
        """
    )

    await add_response(page)
    await page.wait_for_timeout(200)

    assert [event["type"] for event in events].count("done") == 0


@pytest.mark.asyncio
async def test_detached_response_container_does_not_emit_done(observer_page):
    page, events = observer_page
    await page.evaluate(
        """
        () => document.querySelector("#app").innerHTML =
            '<button class="stop-button">active</button>'
        """
    )

    await add_response(page)
    await page.evaluate(
        """
        () => {
            document.querySelector("message-content").remove();
            document.querySelector(".stop-button").style.display = "none";
        }
        """
    )
    await page.wait_for_timeout(200)

    assert [event["type"] for event in events].count("done") == 0


@pytest.mark.asyncio
async def test_hidden_stale_stop_does_not_block_after_active_generation(observer_page):
    page, events = observer_page
    await page.evaluate(
        """
        () => document.querySelector("#app").innerHTML = `
            <button class="stop-button active">active</button>
            <button class="stop-button stale" style="display: none">stale</button>
        `
        """
    )

    await add_response(page)
    await page.evaluate('document.querySelector(".stop-button.active").style.display = "none"')
    await page.wait_for_timeout(150)

    assert [event["type"] for event in events].count("done") == 1


@pytest.mark.asyncio
async def test_completed_response_emits_done_once(observer_page):
    page, events = observer_page
    await page.evaluate(
        """
        () => document.querySelector("#app").innerHTML =
            '<button class="stop-button">active</button>'
        """
    )

    await add_response(page)
    await page.evaluate('document.querySelector(".stop-button").style.display = "none"')
    await page.wait_for_timeout(400)

    assert [event["type"] for event in events].count("done") == 1


@pytest.mark.asyncio
async def test_response_chunks_remain_before_single_done(observer_page):
    page, events = observer_page
    await page.evaluate(
        """
        () => document.querySelector("#app").innerHTML =
            '<button class="stop-button">active</button>'
        """
    )
    await page.evaluate(
        """
        () => {
            const response = document.createElement("message-content");
            document.querySelector("#app").append(response);
        }
        """
    )
    await page.wait_for_timeout(150)
    await page.evaluate('document.querySelector("message-content").textContent = "hello"')
    await page.wait_for_timeout(150)
    await page.evaluate('document.querySelector("message-content").textContent = "hello world"')
    await page.wait_for_timeout(150)
    await page.evaluate('document.querySelector(".stop-button").style.display = "none"')
    await page.wait_for_timeout(300)

    done_indexes = [index for index, event in enumerate(events) if event["type"] == "done"]
    assert len(done_indexes) == 1

    done_index = done_indexes[0]
    text_events = [
        event
        for index, event in enumerate(events)
        if index < done_index and event["type"] in {"chunk", "rewrite"}
    ]
    assert text_events
    assert not any(
        index > done_index and event["type"] in {"chunk", "rewrite"}
        for index, event in enumerate(events)
    )

    response_text = ""
    for event in text_events:
        if event["type"] == "chunk":
            response_text += event["delta"]
        else:
            response_text = event["full_text"]

    assert response_text == "hello world"
