import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.request import OpenAIChatRequest
from app.services.browser.auth_types import AuthStatus
from app.services.factory import ProviderFactory
from app.services.providers.gemini.playwright_adapter import (
    GeminiPlaywrightAdapter,
    PlaywrightRequestState,
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
async def test_stream_done_terminates_with_done_chunk(monkeypatch):
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

    chunks = await collect_stream_chunks(response)
    assert chunks[-1] == "data: [DONE]\n\n"
    assert chunks == ["data: [DONE]\n\n"]


@pytest.mark.asyncio
async def test_stream_queue_overflow_fails_request_without_session_poisoning(monkeypatch):
    page = make_mock_page()
    lease = make_mock_lease(page)
    session = make_mock_session(lease)

    async def submit_prompt(_page, _prompt, _state):
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

    with pytest.raises(Exception) as exc_info:
        await collect_stream_chunks(response)

    assert "Event queue saturated" in str(exc_info.value)
    session.handle_session_failure.assert_not_called()
    lease.close.assert_awaited_once()


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
    request_state = PlaywrightRequestState(request_id="req_1", start_time=0.0)
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
