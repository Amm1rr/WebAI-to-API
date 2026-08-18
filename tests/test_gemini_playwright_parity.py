import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from playwright.async_api import Error as PlaywrightError

from app.schemas.request import OpenAIChatRequest
from app.services.browser.auth_types import AuthStatus
from app.services.browser.errors import (
    BrowserDisconnectedError,
    BrowserGenerationMismatchError,
    BrowserShuttingDownError,
    ConversationBusyError,
    QueueOverflowError,
)
from app.services.factory import ProviderFactory
from app.services.providers.gemini.playwright_adapter import (
    GeminiPlaywrightAdapter,
    BrowserRequestState,
)
from app.services.providers.gemini.provider import GeminiProvider


def make_request(
    *,
    stream: bool,
    conversation_id: str | None = None,
    model: str = "playwright/gemini",
) -> OpenAIChatRequest:
    return OpenAIChatRequest(
        model=model,
        messages=[{"role": "user", "content": "Hello"}],
        conversation_id=conversation_id,
        stream=stream,
    )


def make_mock_page(url: str = "https://gemini.google.com/app") -> MagicMock:
    page = MagicMock()
    page.url = url
    page._gemini_callbacks = {}
    page.goto = AsyncMock()
    page.evaluate = AsyncMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()
    page.is_closed.return_value = False

    input_locator = AsyncMock()
    input_locator.wait_for = AsyncMock()

    generic_locator = MagicMock()
    generic_locator.first = input_locator
    page.locator.return_value = generic_locator
    return page


class CrashablePage(MagicMock):
    """MagicMock page whose url raises a raw PlaywrightError once crashed flag is set."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.crashed = False
        self._crashable_url = "https://gemini.google.com/app"
        self._gemini_callbacks = {}

    @property
    def url(self):
        if self.crashed:
            raise PlaywrightError("Target page, context or browser has been closed")
        return self._crashable_url

    @url.setter
    def url(self, value):
        self._crashable_url = value


def make_crashable_page() -> CrashablePage:
    page = CrashablePage()
    page.goto = AsyncMock()
    page.evaluate = AsyncMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()
    page.is_closed.return_value = False

    input_locator = AsyncMock()
    input_locator.wait_for = AsyncMock()

    generic_locator = MagicMock()
    generic_locator.first = input_locator
    page.locator.return_value = generic_locator
    return page


def make_mock_lease(page: MagicMock, persistent_tab=None) -> MagicMock:
    lease = MagicMock()
    lease.page = page
    lease.persistent_tab = persistent_tab
    lease.close = AsyncMock()
    return lease


def make_mock_session(lease: MagicMock) -> AsyncMock:
    session = AsyncMock()
    session.submit_lock = asyncio.Lock()
    session._setup_page_bridge = AsyncMock()
    session.acquire_lease = AsyncMock(return_value=lease)
    session.handle_session_failure = AsyncMock()
    session.register_conversation = AsyncMock()
    return session


async def emit_bridge_event(page: MagicMock, payload: dict, request_id: str | None = None) -> None:
    callbacks = getattr(page, "_gemini_callbacks", {})
    if request_id is None:
        assert callbacks, "expected a registered Gemini bridge callback"
        request_id = next(iter(callbacks))
    callback = callbacks[request_id]
    payload = dict(payload)
    payload.setdefault("requestId", request_id)
    await callback("gemini", payload)


async def collect_stream_chunks(response: StreamingResponse) -> list[str]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return chunks


async def run_streaming_response(response: StreamingResponse, on_start=None, messages=None) -> list[dict]:
    messages = messages if messages is not None else []

    async def receive():
        await asyncio.sleep(3600)

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start" and on_start:
            on_start()

    await response(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    return messages


def parse_sse_chunk(chunk: str) -> dict:
    assert chunk.startswith("data: ")
    assert chunk.endswith("\n\n")
    return json.loads(chunk[6:-2])


async def configure_playwright_success(
    monkeypatch,
    *,
    page: MagicMock,
    session: AsyncMock,
    submit_side_effect,
    auth_mgr=None,
):
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_engine.get_session = AsyncMock(return_value=session)
    session.engine = mock_engine

    async def mock_get_browser_engine():
        return mock_engine

    monkeypatch.setattr(
        "app.services.providers.gemini.playwright_adapter.get_browser_engine",
        mock_get_browser_engine,
    )

    if auth_mgr is None:
        auth_mgr = MagicMock()
        auth_mgr.coordination_lock.is_locked.return_value = False
        auth_mgr.refresh_playwright_status_lightweight.return_value = AuthStatus.VALID_SESSION

    monkeypatch.setattr(
        "app.services.browser.auth_manager.get_auth_manager",
        lambda: auth_mgr,
    )

    async def check_authentication(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.check_authentication",
        check_authentication,
    )

    async def configure_request_options(_self, *_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.set_extended_thinking",
        configure_request_options,
    )
    async def submit_prompt_bound(_self, *args, **kwargs):
        return await submit_side_effect(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.submit_prompt",
        submit_prompt_bound,
    )

    async def evaluate_side_effect(script, *_args, **_kwargs):
        if "__gemini_bridge" in script:
            await emit_bridge_event(page, {"type": "ready"})
        return None

    page.evaluate = AsyncMock(side_effect=evaluate_side_effect)
    return mock_engine


@pytest.mark.asyncio
async def test_stream_started_confirms_submission_without_emitting_content(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))

    assert isinstance(response, StreamingResponse)
    chunks = await collect_stream_chunks(response)
    assert chunks == ["data: [DONE]\n\n"]


@pytest.mark.asyncio
async def test_buffered_request_fails_promptly_when_page_closes(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ready = asyncio.Event()
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        state_ready.set()
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    request_task = asyncio.create_task(provider.chat_completions(make_request(stream=False)))
    await state_ready.wait()
    close_handler = next(call.args[1] for call in page.on.call_args_list if call.args[0] == "close")

    close_handler(page)

    with pytest.raises(HTTPException) as exc_info:
        await request_task

    assert exc_info.value.status_code == 502
    assert "timed out" not in str(exc_info.value.detail).lower()
    assert any(
        record.message == "Page close detected" and record.levelname == "WARNING"
        for record in caplog.records
    )
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_browser_error_precedes_later_playwright_close_error(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    operation_started = asyncio.Event()
    release_operation = asyncio.Event()

    async def submit_prompt(_page, _prompt, _state):
        operation_started.set()
        await release_operation.wait()
        raise PlaywrightError("Target page, context or browser has been closed")

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    request_task = asyncio.create_task(provider.chat_completions(make_request(stream=False)))
    await operation_started.wait()
    close_handler = next(call.args[1] for call in page.on.call_args_list if call.args[0] == "close")
    close_handler(page)
    release_operation.set()

    with pytest.raises(HTTPException) as exc_info:
        await request_task

    assert exc_info.value.status_code == 502
    assert "timed out" not in str(exc_info.value.detail).lower()
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_page_close_during_application_shutdown_is_not_warning(monkeypatch, caplog):
    page = make_mock_page()
    persistent_tab = MagicMock(browser_generation=1)
    lease = make_mock_lease(page, persistent_tab=persistent_tab)
    session = make_mock_session(lease)
    state_ready = asyncio.Event()
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        state_ready.set()
        return True

    engine = await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )
    engine.shutdown_requested = True

    provider = GeminiProvider()
    request_task = asyncio.create_task(provider.chat_completions(make_request(stream=False)))
    await state_ready.wait()
    close_handler = next(call.args[1] for call in page.on.call_args_list if call.args[0] == "close")

    close_handler(page)

    with pytest.raises(HTTPException) as exc_info:
        await request_task

    assert exc_info.value.status_code == 502
    assert not any(
        record.message == "Page close detected" and record.levelname == "WARNING"
        for record in caplog.records
    )
    assert any(
        record.message == "Page close detected" and record.levelname in {"INFO", "DEBUG"}
        for record in caplog.records
    )
    assert state_ref["state"].page_poisoned is True
    persistent_tab.invalidate.assert_called()
    assert state_ref["state"].terminal_event.is_set()
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_buffered_request_abort_cancels_task_and_releases_lease(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ready = asyncio.Event()
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        state_ready.set()
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    request_task = asyncio.create_task(provider.chat_completions(make_request(stream=False)))
    await state_ready.wait()
    state_ref["state"].abort()

    with pytest.raises(asyncio.CancelledError):
        await request_task

    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_ends_without_done_when_page_crashes(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ready = asyncio.Event()
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        state_ready.set()
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))
    original_wait = provider.playwright_adapter.executor._wait_for_payload
    wait_started = asyncio.Event()

    async def wait_for_payload(*args, **kwargs):
        wait_started.set()
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(provider.playwright_adapter.executor, "_wait_for_payload", wait_for_payload)
    stream_task = asyncio.create_task(collect_stream_chunks(response))
    await wait_started.wait()
    crash_handler = next(call.args[1] for call in page.on.call_args_list if call.args[0] == "crash")

    crash_handler(page)

    chunks = await stream_task

    assert chunks == []
    assert any(
        record.message == "Page crash detected" and record.levelname == "WARNING"
        for record in caplog.records
    )
    assert any(
        record.message == "Stream terminated: " + state_ref["state"].request_id
        and record.reason == "BrowserDisconnectedError"
        for record in caplog.records
    )
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_header_raw_playwright_error_restores_recorded_disconnected(monkeypatch, caplog):
    page = make_crashable_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))
    state = state_ref["state"]

    await emit_bridge_event(page, {"type": "chunk", "delta": "Hello"})

    gen = response.body_iterator
    first = await gen.__anext__()
    assert "Hello" in first

    page.crashed = True
    state.signal_terminal(BrowserDisconnectedError("Browser page crashed during active request"))

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    assert any(
        record.message == "Stream terminated: " + state.request_id
        and record.reason == "BrowserDisconnectedError"
        for record in caplog.records
    )
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_header_raw_playwright_error_restores_recorded_shutting_down(monkeypatch, caplog):
    page = make_crashable_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))
    state = state_ref["state"]

    await emit_bridge_event(page, {"type": "chunk", "delta": "Hello"})

    gen = response.body_iterator
    first = await gen.__anext__()
    assert "Hello" in first

    page.crashed = True
    state.signal_terminal(BrowserShuttingDownError("Browser request aborted during engine shutdown"))

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    assert any(
        record.message == "Stream terminated: " + state.request_id
        and record.reason == "BrowserShuttingDownError"
        for record in caplog.records
    )
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_header_raw_playwright_error_without_terminal_state_propagates(monkeypatch):
    page = make_crashable_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)

    async def submit_prompt(_page, _prompt, _state):
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))

    page.crashed = True

    gen = response.body_iterator
    with pytest.raises(PlaywrightError):
        await gen.__anext__()

    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_response_handles_page_close_after_headers(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}
    request_handles = {}

    session.register_request_abort = MagicMock(side_effect=lambda request_id, signal, abort: request_handles.update({request_id: (signal, abort)}))
    session.unregister_request_abort = MagicMock()

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    response = await GeminiProvider().chat_completions(make_request(stream=True))
    close_handler = next(call.args[1] for call in page.on.call_args_list if call.args[0] == "close")
    messages = await run_streaming_response(response, on_start=lambda: close_handler(page))

    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 200
    assert not any(message.get("body") == b"data: [DONE]\n\n" for message in messages if message["type"] == "http.response.body")
    assert isinstance(state_ref["state"].terminal_error, BrowserDisconnectedError)
    session.unregister_request_abort.assert_called_once_with(state_ref["state"].request_id)
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_response_handles_application_shutdown_page_close(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    engine = await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )
    engine.shutdown_requested = True

    response = await GeminiProvider().chat_completions(make_request(stream=True))
    close_handler = next(call.args[1] for call in page.on.call_args_list if call.args[0] == "close")
    messages = await run_streaming_response(response, on_start=lambda: close_handler(page))

    assert messages[0]["status"] == 200
    assert not any(message.get("body") == b"data: [DONE]\n\n" for message in messages if message["type"] == "http.response.body")
    assert isinstance(state_ref["state"].terminal_error, BrowserDisconnectedError)
    assert not any(record.levelname == "WARNING" and record.message == "Page close detected" for record in caplog.records)
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_response_handles_forced_shutdown_abort(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    response = await GeminiProvider().chat_completions(make_request(stream=True))
    messages = await run_streaming_response(
        response,
        on_start=lambda: state_ref["state"].signal_terminal(
            BrowserShuttingDownError("Browser request aborted during engine shutdown")
        ),
    )

    assert messages[0]["status"] == 200
    assert not any(message.get("body") == b"data: [DONE]\n\n" for message in messages if message["type"] == "http.response.body")
    assert isinstance(state_ref["state"].terminal_error, BrowserShuttingDownError)
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_response_handles_real_request_abort(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}
    request_handles = {}
    payload_wait_started = asyncio.Event()
    messages = []
    created_tasks = []
    real_create_task = asyncio.create_task

    def track_create_task(coro, *args, **kwargs):
        task = real_create_task(coro, *args, **kwargs)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", track_create_task)

    session.register_request_abort = MagicMock(
        side_effect=lambda request_id, signal, abort: request_handles.update({request_id: (signal, abort)})
    )
    session.unregister_request_abort = MagicMock()

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )
    provider = GeminiProvider()
    original_wait = provider.playwright_adapter.executor._wait_for_payload

    async def wait_for_payload(*args, **kwargs):
        payload_wait_started.set()
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(provider.playwright_adapter.executor, "_wait_for_payload", wait_for_payload)
    response = await provider.chat_completions(make_request(stream=True))

    abort_task_ref = {}

    async def abort_after_payload_wait():
        await payload_wait_started.wait()
        request_id = state_ref["state"].request_id
        request_handles[request_id][1]()

    def on_start():
        state_ref["state"].request_task = asyncio.current_task()
        abort_task_ref["task"] = asyncio.create_task(abort_after_payload_wait())

    response_task = asyncio.create_task(run_streaming_response(response, on_start, messages))
    await response_task
    await abort_task_ref["task"]

    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 200
    assert not any(message.get("body") == b"data: [DONE]\n\n" for message in messages if message["type"] == "http.response.body")
    assert isinstance(state_ref["state"].terminal_error, BrowserShuttingDownError)
    session.unregister_request_abort.assert_called_once_with(state_ref["state"].request_id)
    lease.close.assert_awaited_once()
    assert any(record.message == "Stream cancelled: " + state_ref["state"].request_id for record in caplog.records)
    assert not any(record.message == "Stream completed: " + state_ref["state"].request_id for record in caplog.records)
    assert all(task.done() for task in created_tasks)


@pytest.mark.asyncio
async def test_stream_chunk_emits_incremental_sse_delta(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "chunk", "delta": "hello"})
            await emit_bridge_event(page, {"type": "chunk", "delta": " world"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))

    chunks = await collect_stream_chunks(response)
    assert len(chunks) == 3
    assert parse_sse_chunk(chunks[0])["choices"][0]["delta"]["content"] == "hello"
    assert parse_sse_chunk(chunks[1])["choices"][0]["delta"]["content"] == " world"
    assert chunks[2] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_rewrite_emits_only_initial_full_text_before_first_chunk(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "rewrite", "full_text": "Hello world"})
            await emit_bridge_event(page, {"type": "rewrite", "full_text": "Hello world!!"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))

    chunks = await collect_stream_chunks(response)
    assert len(chunks) == 2
    assert parse_sse_chunk(chunks[0])["choices"][0]["delta"]["content"] == "Hello world"
    assert chunks[1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_done_terminates_with_done_chunk(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))

    chunks = await collect_stream_chunks(response)
    assert chunks[-1] == "data: [DONE]\n\n"
    assert chunks == ["data: [DONE]\n\n"]
    request_id = state_ref["state"].request_id
    assert any(record.message == "Stream completed: " + request_id for record in caplog.records)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_request_options_configure_after_model_selection_before_submit(monkeypatch, stream):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    events = []

    async def submit_prompt(_page, _prompt, _state):
        events.append("submit")
        await emit_bridge_event(page, {"type": "done"})
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    hooks = replace(
        provider.playwright_adapter.executor.hooks,
        orchestrate_model_selection=lambda *_args: _record_event(events, "model"),
        configure_request_options=lambda *_args: _record_event(events, "options"),
    )
    provider.playwright_adapter.executor.hooks = hooks

    response = await provider.chat_completions(make_request(stream=stream))
    if stream:
        await collect_stream_chunks(response)

    assert events == ["model", "options", "submit"]


async def _record_event(events, value):
    events.append(value)


@pytest.mark.asyncio
async def test_stream_chunk_timeout_terminates_without_done_after_text(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "chunk", "delta": "hello"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    provider.playwright_adapter.executor.config = replace(
        provider.playwright_adapter.executor.config,
        chunk_timeout=0.01,
    )
    response = await provider.chat_completions(make_request(stream=True))

    chunks = await collect_stream_chunks(response)

    assert len(chunks) == 1
    assert parse_sse_chunk(chunks[0])["choices"][0]["delta"]["content"] == "hello"
    assert not any(chunk == "data: [DONE]\n\n" for chunk in chunks)
    request_id = state_ref["state"].request_id
    terminated = [record for record in caplog.records if record.message == "Stream terminated: " + request_id]
    assert len(terminated) == 1
    assert terminated[0].reason == "chunk_timeout"
    assert not any(record.message == "Stream completed: " + request_id for record in caplog.records)
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_chunk_timeout_terminates_without_payload(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    provider.playwright_adapter.executor.config = replace(
        provider.playwright_adapter.executor.config,
        chunk_timeout=0.01,
    )
    response = await provider.chat_completions(make_request(stream=True))

    assert await collect_stream_chunks(response) == []
    request_id = state_ref["state"].request_id
    terminated = [record for record in caplog.records if record.message == "Stream terminated: " + request_id]
    assert len(terminated) == 1
    assert terminated[0].reason == "chunk_timeout"
    assert lease.close.await_count == 1


@pytest.mark.asyncio
async def test_buffered_chunk_timeout_remains_total_timeout_504(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )

    provider = GeminiProvider()
    provider.playwright_adapter.executor.config = replace(
        provider.playwright_adapter.executor.config,
        total_request_timeout=0.01,
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(make_request(stream=False))

    assert exc_info.value.status_code == 504
    assert lease.close.await_count == 1


@pytest.mark.asyncio
async def test_stream_queue_overflow_fails_request_without_session_poisoning(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            for idx in range(150):
                await emit_bridge_event(page, {"type": "chunk", "delta": f"chunk-{idx}"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))
    await asyncio.sleep(0.05)

    chunks = await collect_stream_chunks(response)

    assert chunks == []
    assert not any("data: [DONE]" in c for c in chunks)
    request_id = state_ref["state"].request_id
    assert any(
        record.message == "Stream terminated: " + request_id
        and record.reason == "QueueOverflowError"
        for record in caplog.records
    )
    session.handle_session_failure.assert_not_called()
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_mid_stream_queue_overflow_terminates_without_done(monkeypatch, caplog):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))
    state = state_ref["state"]

    await emit_bridge_event(page, {"type": "chunk", "delta": "Hello"})

    gen = response.body_iterator
    first = await gen.__anext__()
    assert "Hello" in first
    assert "data: [DONE]" not in first

    state.queue_overflow = True

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    request_id = state.request_id
    assert any(
        record.message == "Stream terminated: " + request_id
        and record.reason == "QueueOverflowError"
        for record in caplog.records
    )
    assert not any(
        record.message == "Stream completed: " + request_id
        for record in caplog.records
    )
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_header_generation_mismatch_terminates_without_done(monkeypatch, caplog):
    persistent_tab = MagicMock(browser_generation=1)
    page = make_mock_page()
    lease = make_mock_lease(page, persistent_tab=persistent_tab)
    session = make_mock_session(lease)
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    engine = await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))
    state = state_ref["state"]
    assert state.active_tab is persistent_tab

    await emit_bridge_event(page, {"type": "chunk", "delta": "Hello"})

    gen = response.body_iterator
    first = await gen.__anext__()
    assert "Hello" in first

    engine.browser_generation = 2
    await emit_bridge_event(page, {"type": "chunk", "delta": "World"})

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    request_id = state.request_id
    assert any(
        record.message == "Stream terminated: " + request_id
        and record.reason == "BrowserGenerationMismatchError"
        for record in caplog.records
    )
    assert not any("data: [DONE]" in c for c in (first,))
    session.handle_session_failure.assert_not_called()
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_header_conversation_busy_error_terminates_without_done(monkeypatch, caplog):
    page = make_mock_page(url="https://gemini.google.com/app/abc123")
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    session.register_conversation = AsyncMock(
        side_effect=ConversationBusyError("Conversation abc123 is busy with another active request.")
    )
    state_ref = {}

    async def submit_prompt(_page, _prompt, state):
        state_ref["state"] = state
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=True))

    gen = response.body_iterator
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    request_id = state_ref["state"].request_id
    assert any(
        record.message == "Stream terminated: " + request_id
        and record.reason == "ConversationBusyError"
        for record in caplog.records
    )
    assert not any("data: [DONE]" in c for c in (await collect_stream_chunks(response)))
    session.handle_session_failure.assert_not_called()
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_header_conversation_busy_returns_409(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    session.acquire_lease = AsyncMock(
        side_effect=ConversationBusyError("Conversation abc123 is busy with another active request.")
    )

    async def submit_prompt(_page, _prompt, _state):
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(make_request(stream=True, conversation_id="abc123"))

    assert excinfo.value.status_code == 409
    session.handle_session_failure.assert_not_called()


@pytest.mark.asyncio
async def test_late_bridge_events_after_cleanup_are_ignored(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    callback_ref = {}

    async def submit_prompt(_page, _prompt, _state):
        callback_ref["callback"] = next(iter(page._gemini_callbacks.values()))

        async def emit_events():
            await asyncio.sleep(0)
            page.url = "https://gemini.google.com/app/abc123"
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "chunk", "delta": "hello"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    session.register_conversation = AsyncMock(return_value=MagicMock(browser_generation=1))
    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=False))

    assert response["choices"][0]["message"]["content"] == "hello"
    assert page._gemini_callbacks == {}
    assert session.register_conversation.await_count == 1

    await callback_ref["callback"]("gemini", {"type": "chunk", "delta": "late", "requestId": "late-id"})
    assert session.register_conversation.await_count == 1
    assert page._gemini_callbacks == {}


@pytest.mark.asyncio
async def test_cleanup_removes_request_callback(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    await provider.chat_completions(make_request(stream=False))

    assert page._gemini_callbacks == {}
    lease.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_executes_observer_stop_script(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    evaluate_scripts = []

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    async def evaluate_side_effect(script, *_args, **_kwargs):
        evaluate_scripts.append(script)
        if "__gemini_bridge" in script:
            await emit_bridge_event(page, {"type": "ready"})
        return None

    page.evaluate = AsyncMock(side_effect=evaluate_side_effect)

    provider = GeminiProvider()
    await provider.chat_completions(make_request(stream=False))

    assert len(evaluate_scripts) >= 2
    assert any("__gemini_bridge" in script for script in evaluate_scripts)
    assert any("__gemini_stop_observer" in script for script in evaluate_scripts)


@pytest.mark.asyncio
async def test_cleanup_removes_close_and_crash_listeners(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    await provider.chat_completions(make_request(stream=False))

    registered_handlers = {}
    for call in page.on.call_args_list:
        event_name, handler = call.args
        registered_handlers[event_name] = handler

    removed_handlers = {}
    for call in page.remove_listener.call_args_list:
        event_name, handler = call.args
        removed_handlers[event_name] = handler

    assert "close" in registered_handlers
    assert "crash" in registered_handlers
    assert "close" in removed_handlers
    assert "crash" in removed_handlers
    assert removed_handlers["close"] is registered_handlers["close"]
    assert removed_handlers["crash"] is registered_handlers["crash"]


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_for_repeated_invocation():
    provider = GeminiProvider()
    adapter = provider.playwright_adapter
    page = make_mock_page()
    request_state = BrowserRequestState(request_id="req_1", start_time=0.0)
    page._gemini_callbacks = {request_state.request_id: AsyncMock()}
    request_state.on_close_handler = MagicMock()
    request_state.on_crash_handler = MagicMock()
    lease = make_mock_lease(page)
    session = AsyncMock()

    never_finishes = asyncio.Event()

    async def observer_coroutine():
        await never_finishes.wait()

    observer_task = asyncio.create_task(observer_coroutine())

    await adapter._cleanup(observer_task, request_state, lease, session)
    await adapter._cleanup(observer_task, request_state, lease, session)

    assert lease.close.await_count == 1
    assert page.remove_listener.call_count == 2
    assert request_state.request_id not in page._gemini_callbacks


@pytest.mark.asyncio
async def test_buffered_request_discovers_conversation_id_from_url_and_returns_it(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    persistent_tab = MagicMock(browser_generation=1)
    session = make_mock_session(lease)
    session.register_conversation = AsyncMock(return_value=persistent_tab)

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            page.url = "https://gemini.google.com/app/abc123"
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "chunk", "delta": "hello"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=False))

    assert response["conversation_id"] == "abc123"
    assert response["reused_conversation"] is False
    session.register_conversation.assert_awaited_once_with("abc123", lease)


@pytest.mark.asyncio
async def test_new_conversation_registration_occurs_once_when_url_becomes_available(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    session.register_conversation = AsyncMock(return_value=MagicMock(browser_generation=1))

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            page.url = "https://gemini.google.com/app/abc123"
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "chunk", "delta": "he"})
            await emit_bridge_event(page, {"type": "chunk", "delta": "llo"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(make_request(stream=False))

    assert response["conversation_id"] == "abc123"
    assert session.register_conversation.await_count == 1


@pytest.mark.asyncio
async def test_reused_persistent_tab_sets_reused_conversation_metadata(monkeypatch):
    page = make_mock_page(url="https://gemini.google.com/app/existing123")
    persistent_tab = MagicMock(browser_generation=1)
    persistent_tab.heartbeat = MagicMock()
    lease = make_mock_lease(page, persistent_tab=persistent_tab)
    session = make_mock_session(lease)

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "chunk", "delta": "hello"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(
        make_request(stream=False, conversation_id="existing123")
    )

    assert response["conversation_id"] == "existing123"
    assert response["reused_conversation"] is True
    page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_continuation_request_navigates_to_conversation_url(monkeypatch):
    page = make_mock_page(url="https://gemini.google.com/app")
    lease = make_mock_lease(page)
    session = make_mock_session(lease)
    session.register_conversation = AsyncMock(return_value=MagicMock(browser_generation=1))

    async def goto_side_effect(url, *args, **kwargs):
        page.url = url
        return None

    page.goto = AsyncMock(side_effect=goto_side_effect)

    async def submit_prompt(_page, _prompt, _state):
        async def emit_events():
            await asyncio.sleep(0)
            await emit_bridge_event(page, {"type": "started"})
            await emit_bridge_event(page, {"type": "chunk", "delta": "hello"})
            await emit_bridge_event(page, {"type": "done"})

        asyncio.create_task(emit_events())
        return True

    await configure_playwright_success(
        monkeypatch,
        page=page,
        session=session,
        submit_side_effect=submit_prompt,
    )

    provider = GeminiProvider()
    response = await provider.chat_completions(
        make_request(stream=False, conversation_id="resume123")
    )

    assert response["conversation_id"] == "resume123"
    page.goto.assert_awaited_once_with(
        "https://gemini.google.com/app/resume123",
        wait_until="domcontentloaded",
        timeout=adapter_config_timeout(),
    )


def adapter_config_timeout() -> int:
    return GeminiProvider().playwright_adapter.config.navigation_timeout


@pytest.mark.asyncio
async def test_request_rejected_when_login_in_progress_with_503(monkeypatch):
    auth_mgr = MagicMock()
    auth_mgr.coordination_lock.is_locked.return_value = True
    auth_mgr.refresh_playwright_status_lightweight.return_value = AuthStatus.VALID_SESSION

    monkeypatch.setattr(
        "app.services.browser.auth_manager.get_auth_manager",
        lambda: auth_mgr,
    )

    get_browser_engine = AsyncMock()
    monkeypatch.setattr(
        "app.services.providers.gemini.playwright_adapter.get_browser_engine",
        get_browser_engine,
    )

    provider = GeminiProvider()
    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(make_request(stream=False))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Authentication in progress."
    get_browser_engine.assert_not_called()


@pytest.mark.asyncio
async def test_request_rejected_when_preflight_auth_expired_with_401(monkeypatch):
    auth_mgr = MagicMock()
    auth_mgr.coordination_lock.is_locked.return_value = False
    auth_mgr.refresh_playwright_status_lightweight.return_value = AuthStatus.EXPIRED_SESSION

    monkeypatch.setattr(
        "app.services.browser.auth_manager.get_auth_manager",
        lambda: auth_mgr,
    )

    get_browser_engine = AsyncMock()
    monkeypatch.setattr(
        "app.services.providers.gemini.playwright_adapter.get_browser_engine",
        get_browser_engine,
    )

    provider = GeminiProvider()
    with pytest.raises(HTTPException) as exc_info:
        await provider.chat_completions(make_request(stream=False))

    assert exc_info.value.status_code == 401
    assert "Authentication expired." in exc_info.value.detail
    assert exc_info.value.headers.get("WWW-Authenticate") == "Bearer"
    get_browser_engine.assert_not_called()


def test_factory_model_prefix_playwright_routes_to_gemini_and_preserves_model_name():
    request = OpenAIChatRequest(messages=[], model="playwright/gemini-3.5-flash")
    provider, model = ProviderFactory.get_provider(request)

    assert isinstance(provider, GeminiProvider)
    assert model == "playwright/gemini-3.5-flash"
