import asyncio
import pytest
from unittest.mock import Mock
from app.services.browser.session import ProviderSession

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
