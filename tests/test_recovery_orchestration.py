import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from app.services.browser.session import ProviderSession
from app.services.browser.errors import TransientSessionError
from app.services.browser.engine import BrowserEngine
from app.services.providers.gemini.provider import GeminiProvider
from app.schemas.request import OpenAIChatRequest
from fastapi import HTTPException

@pytest.mark.asyncio
async def test_recovery_orchestration_atomic_non_blocking(tmp_path):
    # 1. Mock BrowserEngine
    mock_engine = Mock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    
    # 2. Instantiate ProviderSession
    session = ProviderSession(mock_engine, "test_provider")
    
    # Track the number of times the recovery execution runs
    recovery_execution_count = 0
    recovery_started = asyncio.Event()
    recovery_can_finish = asyncio.Event()

    async def mock_do_session_recovery():
        nonlocal recovery_execution_count
        recovery_execution_count += 1
        recovery_started.set()
        await recovery_can_finish.wait()

    # Monkeypatch the recovery execution to our mock
    session._do_session_recovery = mock_do_session_recovery

    # 3. Trigger first recovery
    task1 = asyncio.create_task(session.handle_session_failure())
    
    # Wait until mock_do_session_recovery is entered
    await recovery_started.wait()
    
    # 4. Trigger duplicate concurrent recovery attempts
    # They should exit immediately and not block, even though recovery is not finished!
    # If they were blocking, they would wait for the active recovery task which is currently
    # suspended on recovery_can_finish.wait(). Thus, any blocking behavior would trigger a timeout.
    await asyncio.wait_for(session.handle_session_failure(), timeout=1.0)
    await asyncio.wait_for(session.handle_session_failure(), timeout=1.0)
    
    # 5. Let the first recovery finish
    recovery_can_finish.set()
    await task1
    
    # 6. Verify that only exactly one recovery execution occurred
    assert recovery_execution_count == 1
    
    # 7. Verify that after completion, a new failure correctly spawns a new recovery
    recovery_started.clear()
    recovery_can_finish.clear()
    
    task2 = asyncio.create_task(session.handle_session_failure())
    await recovery_started.wait()
    recovery_can_finish.set()
    await task2
    
    assert recovery_execution_count == 2


@pytest.mark.asyncio
async def test_transient_auth_failure_retry_and_lease_release(monkeypatch):
    """Verify that a transient auth failure during pre-submission triggers retry,

    applies backoff, and successfully releases page leases before retrying.
    """
    # 1. Setup mock classes and objects
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session._setup_page_bridge = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    # Mock get_browser_engine
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    # Mock page and locator
    mock_page = MagicMock()
    mock_page.url = "https://gemini.google.com/app"
    mock_page._gemini_callbacks = {}
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock()
    mock_page.on = MagicMock()
    mock_page.remove_listener = MagicMock()
    
    mock_input_locator = AsyncMock()
    mock_input_locator.wait_for = AsyncMock()
    mock_page.locator.return_value.first = mock_input_locator
    
    # Track the leases acquired and closed
    acquired_leases = []
    closed_leases = []
    
    async def mock_acquire_lease(conversation_id, request_id):
        lease = AsyncMock()
        lease.page = mock_page
        lease.persistent_tab = None
        
        async def mock_close():
            closed_leases.append(lease)
        lease.close = mock_close
        
        acquired_leases.append(lease)
        return lease
        
    mock_session.acquire_lease = mock_acquire_lease
    
    # 2. Mock adapter check_authentication to always fail transiently
    async def mock_check_authentication(*args, **kwargs):
        raise TransientSessionError("Mock transient auth failure")
        
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.check_authentication", mock_check_authentication)
    
    # Mock asyncio.sleep to record sleep delays and bypass actual sleeping
    original_sleep = asyncio.sleep
    sleep_delays = []
    async def mock_sleep(delay):
        sleep_delays.append(delay)
        if delay >= 1.0:
            await original_sleep(0)
        else:
            await original_sleep(delay)
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.asyncio.sleep", mock_sleep)
    
    # 3. Create provider and request
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    # 4. Execute chat_completions; it must raise HTTPException 503 after 3 failed attempts
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 503
    assert "Mock transient auth failure" in str(excinfo.value.detail)
    
    # 5. Verify the retry loop properties:
    # - It must have retried up to 3 times (3 acquisitions)
    assert len(acquired_leases) == 3
    # - Each lease must have been closed during the retry cleanup
    assert len(closed_leases) == 3
    # - It must have applied the correct exponential backoff delays [1.0, 2.0] for the first two failed retries
    backoff_sleeps = [d for d in sleep_delays if d >= 1.0]
    assert backoff_sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_transient_failure_preserves_persistent_state_file(tmp_path):
    """Verify that a session recovery event does NOT delete the persistent auth state file."""
    # 1. Mock BrowserEngine
    mock_engine = MagicMock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    
    # 2. Instantiate ProviderSession
    session = ProviderSession(mock_engine, "test_provider")
    
    # Create a dummy state file at a state_path
    state_file = tmp_path / "gemini.json"
    state_file.write_text('{"cookies": [{"name": "mock"}]}')
    session.state_path = str(state_file)
    
    # Mock close_resources to not perform real teardowns in unit tests
    session.close_resources = AsyncMock()
    
    # 3. Trigger session recovery
    await session.handle_session_failure()
    if session._recovery_task:
        await session._recovery_task
        
    # 4. Verify that the state file is preserved
    assert state_file.exists()
    assert state_file.read_text() == '{"cookies": [{"name": "mock"}]}'


@pytest.mark.asyncio
async def test_intentional_provider_context_cleanup_does_not_shutdown_engine(tmp_path):
    mock_engine = MagicMock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    mock_engine._on_browser_disconnected = Mock()

    session = ProviderSession(mock_engine, "test_provider")
    context = MagicMock()
    async def close_context():
        await session._on_context_closed(context)
    context.close = AsyncMock(side_effect=close_context)
    session.context = context

    await session.close_resources(save_state=False)

    mock_engine._on_browser_disconnected.assert_not_called()
    assert session.context is None


@pytest.mark.asyncio
async def test_unexpected_provider_context_close_triggers_engine_shutdown(tmp_path):
    mock_engine = MagicMock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    mock_engine._on_browser_disconnected = Mock()

    session = ProviderSession(mock_engine, "test_provider")
    context = MagicMock()

    await session._on_context_closed(context)

    mock_engine._on_browser_disconnected.assert_called_once()


@pytest.mark.asyncio
async def test_browser_disconnect_still_schedules_terminal_close():
    engine = BrowserEngine(headless=True)
    engine.close = AsyncMock()

    engine._on_browser_disconnected()
    await asyncio.sleep(0)

    engine.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_keepalive_liveness_loss_still_triggers_terminal_shutdown(tmp_path, monkeypatch):
    mock_engine = MagicMock()
    mock_engine.max_pages = 5
    mock_engine.user_data_dir = str(tmp_path)
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    mock_engine._on_browser_disconnected = Mock()

    session = ProviderSession(mock_engine, "test_provider")
    session.last_browser_generation = 1

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("app.services.browser.session.asyncio.sleep", no_sleep)

    await session._reaper_loop()

    mock_engine._on_browser_disconnected.assert_called_once()


@pytest.mark.asyncio
async def test_post_submission_no_retry(monkeypatch):
    """Verify that failures occurring after the submission boundary (prompt submit)

    never trigger any automatic retries and propagate immediately.
    """
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session._setup_page_bridge = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    mock_page = MagicMock()
    mock_page.url = "https://gemini.google.com/app"
    mock_page._gemini_callbacks = {}
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock()
    mock_page.on = MagicMock()
    mock_page.remove_listener = MagicMock()
    
    mock_input_locator = AsyncMock()
    mock_input_locator.wait_for = AsyncMock()
    mock_page.locator.return_value.first = mock_input_locator
    
    acquired_leases = []
    closed_leases = []
    
    async def mock_acquire_lease(conversation_id, request_id):
        lease = AsyncMock()
        lease.page = mock_page
        lease.persistent_tab = None
        
        async def mock_close():
            closed_leases.append(lease)
        lease.close = mock_close
        
        acquired_leases.append(lease)
        return lease
        
    mock_session.acquire_lease = mock_acquire_lease
    
    # Pre-submission checks succeed on 1st attempt
    async def mock_check_authentication(*args, **kwargs):
        return True
        
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.check_authentication", mock_check_authentication)
    
    # Mock submit_prompt to deliberately raise an exception to simulate post-submission failure
    submit_calls = 0
    async def mock_submit_prompt(*args, **kwargs):
        nonlocal submit_calls
        submit_calls += 1
        raise RuntimeError("Post-submission failure")
        
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.submit_prompt", mock_submit_prompt)
    
    # Mock state.js_ready.set() to trigger instantly when evaluate is called for the observer script
    async def mock_evaluate(script, *args, **kwargs):
        callbacks = list(mock_page._gemini_callbacks.values())
        if callbacks:
            await callbacks[0]("gemini", {"type": "ready"})
            
    mock_page.evaluate = mock_evaluate
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    # Execute chat_completions; it must raise HTTPException (due to post-submission failure propagation)
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Internal server error."
    
    # Verify no retries occurred:
    # - Only exactly 1 lease acquired
    assert len(acquired_leases) == 1
    # - Only exactly 1 submit attempt occurred
    assert submit_calls == 1
    # - Cleanup was executed correctly
    assert len(closed_leases) == 1


@pytest.mark.asyncio
async def test_observer_leak_prevention(monkeypatch):
    """Verify that failed retries do not leave dangling observer tasks alive in the event loop."""
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session._setup_page_bridge = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    mock_page = MagicMock()
    mock_page.url = "https://gemini.google.com/app"
    mock_page._gemini_callbacks = {}
    mock_page.goto = AsyncMock()
    
    # We want page.evaluate to return a mock coroutine that runs indefinitely until cancelled
    # so we can verify if it was cancelled.
    observer_run_event = asyncio.Event()
    observer_cancelled_event = asyncio.Event()
    
    async def mock_evaluate(script, *args, **kwargs):
        observer_run_event.set()
        try:
            # Sleep indefinitely to simulate long running observer evaluation
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            observer_cancelled_event.set()
            raise
            
    mock_page.evaluate = mock_evaluate
    mock_page.on = MagicMock()
    mock_page.remove_listener = MagicMock()
    
    mock_input_locator = AsyncMock()
    mock_input_locator.wait_for = AsyncMock()
    mock_page.locator.return_value.first = mock_input_locator
    
    async def mock_acquire_lease(conversation_id, request_id):
        lease = MagicMock()
        lease.page = mock_page
        lease.persistent_tab = None
        lease.close = AsyncMock()
        return lease
        
    mock_session.acquire_lease = mock_acquire_lease
    
    async def mock_check_authentication(*args, **kwargs):
        return True
        
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.check_authentication", mock_check_authentication)
    
    # Patch asyncio.sleep to not actually wait during retry backoff
    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        if delay >= 1.0:
            await original_sleep(0)
        else:
            await original_sleep(delay)
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.asyncio.sleep", mock_sleep)
    
    # Patch asyncio.timeout to timeout instantly
    class InstantTimeout:
        def __init__(self, delay):
            pass
        async def __aenter__(self):
            raise asyncio.TimeoutError()
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.asyncio.timeout", InstantTimeout)
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    # Execute chat_completions; it must fail after 3 attempts due to the timeout
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 503
        
    # Verify that the observer was started and then successfully cancelled
    assert observer_run_event.is_set()
    assert observer_cancelled_event.is_set()


@pytest.mark.asyncio
async def test_auth_expired_maps_to_401(monkeypatch):
    """Verify that SessionNotAliveError maps to HTTPException 401."""
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_engine.is_shutting_down = False
    mock_engine._on_browser_disconnected = Mock()
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session._setup_page_bridge = AsyncMock()
    mock_session.handle_session_failure = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    mock_page = MagicMock()
    mock_page.url = "https://gemini.google.com/app"
    mock_page._gemini_callbacks = {}
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock()
    mock_page.on = MagicMock()
    mock_page.remove_listener = MagicMock()
    
    mock_input_locator = AsyncMock()
    mock_input_locator.wait_for = AsyncMock()
    mock_page.locator.return_value.first = mock_input_locator
    
    async def mock_acquire_lease(conversation_id, request_id):
        lease = MagicMock()
        lease.page = mock_page
        lease.persistent_tab = None
        lease.close = AsyncMock()
        return lease
    mock_session.acquire_lease = mock_acquire_lease
    
    # Mock adapter check_authentication to return False (auth expired)
    async def mock_check_authentication(*args, **kwargs):
        return False
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.check_authentication", mock_check_authentication)
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 401
    assert "Authentication expired." in excinfo.value.detail
    mock_session.handle_session_failure.assert_awaited_once()
    mock_engine._on_browser_disconnected.assert_not_called()
    assert mock_engine.is_shutting_down is False


@pytest.mark.asyncio
async def test_transient_failure_maps_to_503(monkeypatch):
    """Verify that TransientSessionError maps to HTTPException 503."""
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session._setup_page_bridge = AsyncMock()
    mock_engine.get_session = MagicMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    mock_page = MagicMock()
    mock_page.url = "https://gemini.google.com/app"
    mock_page._gemini_callbacks = {}
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock()
    mock_page.on = MagicMock()
    mock_page.remove_listener = MagicMock()
    
    mock_input_locator = AsyncMock()
    mock_input_locator.wait_for = AsyncMock()
    mock_page.locator.return_value.first = mock_input_locator
    
    async def mock_acquire_lease(conversation_id, request_id):
        lease = MagicMock()
        lease.page = mock_page
        lease.persistent_tab = None
        lease.close = AsyncMock()
        return lease
    mock_session.acquire_lease = mock_acquire_lease
    
    # Mock adapter check_authentication to raise TransientSessionError
    async def mock_check_authentication(*args, **kwargs):
        from app.services.browser.errors import TransientSessionError
        raise TransientSessionError("Mock transient error")
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.GeminiProviderAdapter.check_authentication", mock_check_authentication)
    
    # Bypass retry sleep
    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        await original_sleep(0)
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.asyncio.sleep", mock_sleep)
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 503
    assert "Mock transient error" in excinfo.value.detail


@pytest.mark.asyncio
async def test_timeout_maps_to_504(monkeypatch):
    """Verify that asyncio.TimeoutError maps to HTTPException 504."""
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session._setup_page_bridge = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    mock_page = MagicMock()
    mock_page.url = "https://gemini.google.com/app"
    mock_page._gemini_callbacks = {}
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock()
    mock_page.on = MagicMock()
    mock_page.remove_listener = MagicMock()
    
    # Mock acquire_lease to raise asyncio.TimeoutError directly
    async def mock_acquire_lease(conversation_id, request_id):
        raise asyncio.TimeoutError()
    mock_session.acquire_lease = mock_acquire_lease
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 504
    assert "Request timed out." in excinfo.value.detail


@pytest.mark.asyncio
async def test_unknown_exception_maps_to_500(monkeypatch):
    """Verify that unexpected exceptions map to HTTPException 500 with a secure internal error message."""
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session._setup_page_bridge = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    # Make acquire_lease raise an unexpected exception
    async def mock_acquire_lease(conversation_id, request_id):
        raise ValueError("Secret database password failed")
    mock_session.acquire_lease = mock_acquire_lease
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 500
    # Crucial security check: internal traceback/raw message must not leak to details
    assert "Internal server error." in excinfo.value.detail
    assert "Secret database password" not in excinfo.value.detail


@pytest.mark.asyncio
async def test_exception_before_retry_loop_initializes_state(monkeypatch):
    """Verify that an exception raised before the retry loop (when state is None)
    maps properly and does not trigger UnboundLocalError during error handling/cleanup.
    """
    async def mock_get_browser_engine():
        raise RuntimeError("Failure before retry loop")
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 500
    assert "Internal server error." in excinfo.value.detail


@pytest.mark.asyncio
async def test_browser_disconnected_error_poisons_session(monkeypatch):
    """Verify that BrowserDisconnectedError poisons the session and maps to 502."""
    from app.services.browser.errors import BrowserDisconnectedError
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session.handle_session_failure = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    async def mock_acquire_lease(conversation_id, request_id):
        raise BrowserDisconnectedError("Process died")
    mock_session.acquire_lease = mock_acquire_lease
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 502
    assert "Underlying browser process disconnected." in excinfo.value.detail
    mock_session.handle_session_failure.assert_called_once()


@pytest.mark.asyncio
async def test_browser_generation_mismatch_error_poisons_session(monkeypatch):
    """Verify that BrowserGenerationMismatchError poisons the session and maps to 503."""
    from app.services.browser.errors import BrowserGenerationMismatchError
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session.handle_session_failure = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    async def mock_acquire_lease(conversation_id, request_id):
        raise BrowserGenerationMismatchError("Mismatch occurred")
    mock_session.acquire_lease = mock_acquire_lease
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 503
    assert "Browser generation rollover mismatch." in excinfo.value.detail
    mock_session.handle_session_failure.assert_called_once()


@pytest.mark.asyncio
async def test_playwright_closed_page_error_maps_to_503(monkeypatch):
    """Verify that closed page/context PlaywrightError maps to 503 'Browser session unavailable.'."""
    from playwright.async_api import Error as PlaywrightError
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    async def mock_acquire_lease(conversation_id, request_id):
        raise PlaywrightError("Target page, context or browser has been closed")
    mock_session.acquire_lease = mock_acquire_lease
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 503
    assert "Browser session unavailable." in excinfo.value.detail


@pytest.mark.asyncio
async def test_generic_playwright_error_maps_to_502(monkeypatch):
    """Verify that generic PlaywrightError maps to 502 'Browser interaction failure.'."""
    from playwright.async_api import Error as PlaywrightError
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    async def mock_acquire_lease(conversation_id, request_id):
        raise PlaywrightError("Failed to click element due to overlap")
    mock_session.acquire_lease = mock_acquire_lease
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 502
    assert "Browser interaction failure." in excinfo.value.detail


@pytest.mark.asyncio
async def test_www_authenticate_header_exists_on_401(monkeypatch):
    """Verify that SessionNotAliveError includes WWW-Authenticate header in 401 response."""
    from app.services.browser.errors import SessionNotAliveError
    mock_engine = MagicMock()
    mock_engine.browser_generation = 1
    mock_session = AsyncMock()
    mock_session.submit_lock = asyncio.Lock()
    mock_session.handle_session_failure = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=mock_session)
    
    async def mock_get_browser_engine():
        return mock_engine
    monkeypatch.setattr("app.services.providers.gemini.playwright_adapter.get_browser_engine", mock_get_browser_engine)
    
    async def mock_acquire_lease(conversation_id, request_id):
        raise SessionNotAliveError("Expired session cookies")
    mock_session.acquire_lease = mock_acquire_lease
    
    provider = GeminiProvider()
    request = OpenAIChatRequest(
        model="playwright/gemini",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False
    )
    
    with pytest.raises(HTTPException) as excinfo:
        await provider.chat_completions(request)
        
    assert excinfo.value.status_code == 401
    assert "Authentication expired." in excinfo.value.detail
    assert excinfo.value.headers is not None
    assert excinfo.value.headers.get("WWW-Authenticate") == "Bearer"

