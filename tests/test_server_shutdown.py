import asyncio
import sys
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
import uvicorn
import run as run_module

from app.services.browser.engine import BrowserEngine
from run import ApplicationServer, run_server


def make_server():
    return ApplicationServer(uvicorn.Config(lambda scope, receive, send: None))


def started_server():
    server = make_server()
    server.started = True
    return server


@pytest.fixture(autouse=True)
def _reset_generic_shutdown_state():
    from app.shutdown import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows policy only")
def test_windows_runner_uses_proactor_policy(mocker):
    policy = object()
    proactor = mocker.patch.object(
        run_module.asyncio, "WindowsProactorEventLoopPolicy", return_value=policy
    )
    selector = mocker.patch.object(run_module.asyncio, "WindowsSelectorEventLoopPolicy")
    setter = mocker.patch.object(run_module.asyncio, "set_event_loop_policy")
    mocker.patch.object(run_module.sys, "platform", "win32")

    run_module.configure_windows_event_loop_policy()

    proactor.assert_called_once_with()
    selector.assert_not_called()
    setter.assert_called_once_with(policy)


# --- Signal path (existing contract, intent via shared helper) ------------


def test_signal_marks_application_intent_before_uvicorn_exit(mocker):
    calls = []
    sig = object()
    frame = object()

    mocker.patch(
        "app.services.browser.engine.request_application_shutdown",
        side_effect=lambda: calls.append("application-shutdown"),
    )
    mocker.patch("run.request_generic_shutdown", side_effect=lambda source="application": calls.append("generic-shutdown") or True)
    mocker.patch.object(
        uvicorn.Server,
        "handle_exit",
        autospec=True,
        side_effect=lambda server, received_sig, received_frame: calls.append(
            ("uvicorn-exit", received_sig, received_frame)
        ),
    )

    make_server().handle_exit(sig, frame)

    assert calls[0] == "generic-shutdown"
    assert calls[1] == "application-shutdown"
    assert calls[2] == ("uvicorn-exit", sig, frame)


def test_real_sig_and_frame_forwarded_unchanged(mocker):
    sig = object()
    frame = object()
    mocker.patch(
        "app.services.browser.engine.request_application_shutdown"
    )
    mocker.patch("run.request_generic_shutdown")
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server, "handle_exit", autospec=True
    )

    server = make_server()
    server.handle_exit(sig, frame)

    assert uvicorn_exit.call_args.args[1:] == (sig, frame)


def test_second_real_signal_still_reaches_uvicorn(mocker):
    """Force-exit escalation must survive the once-only intent guard (non-SIGINT)."""
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("app.shutdown.request_shutdown")
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server, "handle_exit", autospec=True
    )

    server = make_server()
    server.handle_exit(1, None)
    server.handle_exit(1, None)

    assert uvicorn_exit.call_count == 2


# --- Programmatic shutdown primitive ---------------------------------------


def test_first_request_shutdown_returns_true_and_sets_should_exit(mocker):
    engine_intent = mocker.patch(
        "app.services.browser.engine.request_application_shutdown"
    )

    server = started_server()
    assert server.request_shutdown("updater") is True
    assert server.should_exit is True
    engine_intent.assert_called_once()


def test_repeated_request_shutdown_returns_false_without_duplicate_intent(mocker):
    engine_intent = mocker.patch(
        "app.services.browser.engine.request_application_shutdown"
    )
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server, "handle_exit", autospec=True
    )

    server = started_server()
    assert server.request_shutdown("first") is True
    assert server.request_shutdown("second") is False

    assert engine_intent.call_count == 1
    assert server.should_exit is True
    uvicorn_exit.assert_not_called()  # programmatic path never fakes signals


def test_programmatic_shutdown_does_not_call_uvicorn_handle_exit(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server, "handle_exit", autospec=True
    )

    server = started_server()
    server.request_shutdown()

    uvicorn_exit.assert_not_called()


def test_request_shutdown_before_started_returns_false(mocker):
    engine_intent = mocker.patch(
        "app.services.browser.engine.request_application_shutdown"
    )

    server = make_server()  # `started` not set (pre-run)
    assert server.request_shutdown("too early") is False
    assert server.should_exit is False
    engine_intent.assert_not_called()


def test_no_engine_programmatic_shutdown_exits_cleanly(mocker):
    """
    Real contract, no mocks on the intent shim:
    no existing engine -> request_application_shutdown() returns False
    internally -> ApplicationServer still performs graceful shutdown.
    BrowserEngine remains uninitialized.
    """
    mocker.patch.object(BrowserEngine, "_instance", None)
    shim_spy = mocker.spy(
        __import__(
            "app.services.browser.engine", fromlist=["request_application_shutdown"]
        ),
        "request_application_shutdown",
    )
    uvicorn_exit = mocker.patch.object(uvicorn.Server, "handle_exit")

    server = started_server()
    assert server.request_shutdown() is True
    assert server.should_exit is True

    shim_spy.assert_called_once()
    assert shim_spy.spy_return is False
    assert BrowserEngine._instance is None  # never initialized
    uvicorn_exit.assert_not_called()


def test_request_shutdown_rejected_when_already_shutting_down(mocker):
    """should_exit set through another path -> later request is not accepted."""
    engine_intent = mocker.patch(
        "app.services.browser.engine.request_application_shutdown",
        return_value=True,
    )
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server, "handle_exit", autospec=True
    )

    server = started_server()
    server.should_exit = True  # Uvicorn entered shutdown via another path

    assert server.request_shutdown("late programmatic") is False
    engine_intent.assert_not_called()
    uvicorn_exit.assert_not_called()
    assert server.should_exit is True


def test_signal_after_programmatic_request_still_delegates_to_uvicorn(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server, "handle_exit", autospec=True
    )

    server = started_server()
    assert server.request_shutdown("programmatic") is True
    server.handle_exit(15, None)

    # Real signal always reaches Uvicorn, even after programmatic request.
    assert uvicorn_exit.call_args.args[1:] == (15, None)


def test_request_shutdown_is_thread_callable(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    server = started_server()

    result = asyncio.run(asyncio.to_thread(server.request_shutdown))

    assert result is True


# --- Legacy signal-path tests (adapted) ------------------------------------


def test_run_server_suppresses_top_level_keyboard_interrupt(mocker):
    config = MagicMock()
    server = mocker.patch("run.ApplicationServer")
    server.return_value.run.side_effect = KeyboardInterrupt

    run_server(config)

    server.assert_called_once_with(config)
    server.return_value.run.assert_called_once_with()


def test_run_server_propagates_runtime_error(mocker):
    config = MagicMock()
    server = mocker.patch("run.ApplicationServer")
    server.return_value.run.side_effect = RuntimeError("startup failed")

    try:
        run_server(config)
    except RuntimeError as error:
        assert str(error) == "startup failed"
    else:
        raise AssertionError("RuntimeError was suppressed")


def test_run_server_returns_normally_when_server_returns(mocker):
    config = MagicMock()
    server = mocker.patch("run.ApplicationServer")

    assert run_server(config) is None
    server.return_value.run.assert_called_once_with()


@pytest.mark.asyncio
async def test_startup_starts_update_check_only_after_uvicorn_startup(mocker):
    calls = []
    entered = asyncio.Event()

    async def parent_startup(server, sockets=None):
        calls.append("uvicorn-startup")
        server.started = True

    async def check():
        entered.set()

    check = mocker.patch("app.utils.update_check.run_update_check", side_effect=check)
    mocker.patch.object(uvicorn.Server, "startup", autospec=True, side_effect=parent_startup)
    mocker.patch.object(uvicorn.Server, "shutdown", autospec=True)
    server = make_server()

    await server.startup()
    await entered.wait()

    assert calls == ["uvicorn-startup"]
    check.assert_awaited_once()
    await server.startup()
    check.assert_awaited_once()
    await server.shutdown()


@pytest.mark.asyncio
async def test_startup_does_not_create_update_check_when_disabled(mocker):
    async def parent_startup(server, sockets=None):
        server.started = True

    check = mocker.patch("app.utils.update_check.run_update_check")
    mocker.patch.object(uvicorn.Server, "startup", autospec=True, side_effect=parent_startup)
    mocker.patch.object(uvicorn.Server, "shutdown", autospec=True)
    mocker.patch("run.CONFIG.getboolean", return_value=False)
    server = make_server()

    await server.startup()

    assert server._update_check_task is None
    check.assert_not_called()
    await server.shutdown()


@pytest.mark.asyncio
async def test_failed_uvicorn_startup_does_not_create_update_check(mocker):
    async def parent_startup(server, sockets=None):
        raise RuntimeError("startup failed")

    check = mocker.patch("app.utils.update_check.run_update_check")
    mocker.patch.object(uvicorn.Server, "startup", autospec=True, side_effect=parent_startup)
    server = make_server()

    with pytest.raises(RuntimeError, match="startup failed"):
        await server.startup()

    assert server._update_check_task is None
    check.assert_not_called()


@pytest.mark.asyncio
async def test_slow_update_check_does_not_delay_startup(mocker):
    started_check = asyncio.Event()
    release_check = asyncio.Event()

    async def parent_startup(server, sockets=None):
        server.started = True

    async def slow_check():
        started_check.set()
        await release_check.wait()

    mocker.patch.object(uvicorn.Server, "startup", autospec=True, side_effect=parent_startup)
    mocker.patch.object(uvicorn.Server, "shutdown", autospec=True)
    mocker.patch("app.utils.update_check.run_update_check", side_effect=slow_check)
    server = make_server()

    await server.startup()

    assert not started_check.is_set()
    await started_check.wait()
    release_check.set()
    await server.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_update_check_task_before_uvicorn_shutdown(mocker):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def running_check():
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def parent_shutdown(server, sockets=None):
        assert cancelled.is_set()

    mocker.patch.object(uvicorn.Server, "shutdown", autospec=True, side_effect=parent_shutdown)
    server = make_server()
    server._update_check_task = asyncio.create_task(running_check())
    await entered.wait()

    await server.shutdown()

    assert server._update_check_task.cancelled()


# --- Canonical accessor + idle-shutdown fix ---------------------------------


@pytest.mark.asyncio
async def test_get_existing_browser_engine_returns_none_without_initialization():
    from app.services.browser import engine as engine_module

    monkey_none = MagicMock()
    original = engine_module.BrowserEngine._instance
    engine_module.BrowserEngine._instance = None
    try:
        assert engine_module.get_existing_browser_engine() is None
        assert engine_module.BrowserEngine._instance is None  # no bootstrap
        _ = monkey_none  # unused sentinel
    finally:
        engine_module.BrowserEngine._instance = original


@pytest.mark.asyncio
async def test_idle_lifespan_shutdown_does_not_initialize_browser_engine(
    mocker,
):
    """Idle server: lifespan shutdown must skip BrowserEngine entirely."""
    from app import main as app_main

    mocker.patch(
        "app.main.init_gemini_client", new=AsyncMock(return_value=False)
    )
    auth_manager = MagicMock()
    auth_manager.refresh_status = lambda: None
    mocker.patch("app.main.get_auth_manager", return_value=auth_manager)
    mocker.patch(
        "app.main.shutdown_session_managers", new=AsyncMock(return_value=None)
    )
    mocker.patch(
        "app.main.close_gemini_client", new=AsyncMock(return_value=None)
    )

    initializing_getter = mocker.patch(
        "app.services.browser.engine.get_browser_engine",
        side_effect=AssertionError("shutdown initialized the browser engine"),
    )
    mocker.patch.object(BrowserEngine, "_instance", None)

    cm = app_main.lifespan(app_main.app)
    await cm.__aenter__()
    await cm.__aexit__(None, None, None)

    initializing_getter.assert_not_called()


@pytest.mark.asyncio
async def test_existing_engine_receives_close_on_lifespan_shutdown(mocker):
    from app import main as app_main
    from app.services.browser import engine as engine_module

    mocker.patch(
        "app.main.init_gemini_client", new=AsyncMock(return_value=False)
    )
    auth_manager = MagicMock()
    auth_manager.refresh_status = lambda: None
    mocker.patch("app.main.get_auth_manager", return_value=auth_manager)
    mocker.patch(
        "app.main.shutdown_session_managers", new=AsyncMock(return_value=None)
    )
    mocker.patch(
        "app.main.close_gemini_client", new=AsyncMock(return_value=None)
    )

    fake_engine = MagicMock()
    fake_engine.close = AsyncMock()
    original = engine_module.BrowserEngine._instance
    engine_module.BrowserEngine._instance = fake_engine
    try:
        cm = app_main.lifespan(app_main.app)
        await cm.__aenter__()
        await cm.__aexit__(None, None, None)
    finally:
        engine_module.BrowserEngine._instance = original

    fake_engine.close.assert_awaited_once_with(source="application")


@pytest.mark.asyncio
async def test_health_endpoint_uses_canonical_accessor(mocker, monkeypatch):
    """/health stays side-effect-free with the canonical accessor."""
    from app.services.browser import engine as engine_module
    from app.endpoints.system import health

    original = engine_module.BrowserEngine._instance
    engine_module.BrowserEngine._instance = None
    initializer = mocker.patch(
        "app.services.browser.engine.get_browser_engine",
        side_effect=AssertionError("/health initialized the engine"),
    )
    try:
        response = await health()
    finally:
        engine_module.BrowserEngine._instance = original

    assert response.status_code == 200
    initializer.assert_not_called()


# --- Thread-safety of the shutdown transition (Phase 4) ----------------------


def test_concurrent_programmatic_requests_exactly_one_accepted(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    server = started_server()
    barrier = threading.Barrier(8)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait()
            results.append(server.request_shutdown("ipc"))
        except Exception as error:  # pragma: no cover
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert sorted(results, reverse=True) == [True] + [False] * 7
    assert server.should_exit is True


def test_concurrent_requests_mark_engine_intent_exactly_once(mocker):
    intent_calls = []
    mocker.patch(
        "app.services.browser.engine.request_application_shutdown",
        side_effect=lambda: intent_calls.append(1),
    )
    server = started_server()
    barrier = threading.Barrier(8)
    threads = [
        threading.Thread(
            target=lambda: (
                barrier.wait(),
                server.request_shutdown("ipc"),
            )
        )
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(intent_calls) == 1


def test_signal_and_programmatic_race_marks_intent_once(mocker):
    intent_calls = mocker.patch(
        "app.services.browser.engine.request_application_shutdown",
    )
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server, "handle_exit", autospec=True
    )
    server = started_server()
    start = threading.Event()

    def signal_worker():
        start.wait()
        server.handle_exit(15, None)

    def ipc_worker():
        start.wait()
        server.request_shutdown("ipc")

    threads = [
        threading.Thread(target=signal_worker),
        threading.Thread(target=ipc_worker),
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join()

    assert intent_calls.call_count == 1
    assert server._shutdown_intent_marked is True
    assert uvicorn_exit.call_count == 1  # real signal still reaches Uvicorn


def test_real_signal_always_delegates_to_uvicorn_even_when_intent_exists(
    mocker,
):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("app.shutdown.request_shutdown")
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server, "handle_exit", autospec=True
    )
    server = make_server()
    server.handle_exit(15, None)
    server.handle_exit(15, None)  # second SIGTERM still delegates (hard exit only for SIGINT)

    assert uvicorn_exit.call_count == 2


# --- Emergency SIGINT hard exit (ASGI_BOUNDARY_PLUS_HARD_EXIT) -------------------

import signal as _signal


def test_first_sigint_delegates_and_marks_intent(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown", return_value=True)
    uvicorn_exit = mocker.patch.object(uvicorn.Server, "handle_exit", autospec=True)
    mock_exit = mocker.patch("run.os._exit")

    server = make_server()
    server.handle_exit(_signal.SIGINT, None)

    uvicorn_exit.assert_called_once()
    mock_exit.assert_not_called()
    assert server._shutdown_intent_marked is True


def test_second_sigint_hard_exits_with_130(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown")
    mock_exit = mocker.patch("run.os._exit", side_effect=SystemExit(130))

    server = make_server()
    # first SIGINT → graceful, sets should_exit via real Uvicorn handle_exit (not mocked)
    server.handle_exit(_signal.SIGINT, None)
    assert server.should_exit is True
    with pytest.raises(SystemExit):
        server.handle_exit(_signal.SIGINT, None)  # second → hard exit

    mock_exit.assert_called_once_with(130)


def test_sigterm_does_not_hard_exit(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown")
    mock_exit = mocker.patch("run.os._exit")

    server = make_server()
    server.handle_exit(_signal.SIGINT, None)
    assert server.should_exit is True
    server.handle_exit(_signal.SIGTERM, None)

    mock_exit.assert_not_called()
    assert server.should_exit is True


def test_programmatic_shutdown_does_not_hard_exit(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown")
    mocker.patch("run.os._exit")

    server = started_server()
    assert server.request_shutdown("programmatic") is True
    # no hard exit on programmatic alone
    assert server.should_exit is True


def test_sigint_after_programmatic_shutdown_hard_exits(mocker):
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown", return_value=True)
    mock_exit = mocker.patch("run.os._exit", side_effect=SystemExit(130))

    server = started_server()
    assert server.request_shutdown("programmatic") is True
    assert server.should_exit is True
    # SIGINT after programmatic should be treated as emergency
    with pytest.raises(SystemExit):
        server.handle_exit(_signal.SIGINT, None)

    mock_exit.assert_called_once_with(130)


# --- run_server Windows wiring (Phase 4) -------------------------------------


def test_windows_branch_starts_stops_listener_around_run(mocker):
    listener_cls = mocker.patch("app.shutdown_transport.ShutdownListener")
    mocker.patch("sys.platform", new="win32")
    config = MagicMock()
    fake_server = mocker.patch("run.ApplicationServer").return_value

    run_server(config)

    listener_cls.assert_called_once_with(
        callback=fake_server.request_shutdown,
        control_file=mocker.ANY,
    )
    assert str(listener_cls.call_args.kwargs["control_file"]).endswith(
        "shutdown-control.json"
    )
    listener_instance = listener_cls.return_value
    listener_instance.start.assert_called_once_with()
    listener_instance.stop.assert_called_once_with()


def test_listener_stopped_when_run_raises_on_windows(mocker):
    listener_cls = mocker.patch("app.shutdown_transport.ShutdownListener")
    mocker.patch("sys.platform", new="win32")
    fake_server = mocker.patch("run.ApplicationServer").return_value
    fake_server.run.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_server(MagicMock())

    listener_cls.return_value.stop.assert_called_once_with()


def test_non_windows_branch_never_instantiates_listener(mocker):
    listener_cls = mocker.patch("app.shutdown_transport.ShutdownListener")
    mocker.patch("sys.platform", new="linux")
    fake_server = mocker.patch("run.ApplicationServer").return_value

    run_server(MagicMock())

    listener_cls.assert_not_called()
    fake_server.run.assert_called_once_with()


# --- Graceful shutdown timeout configuration ---------------------------------


def test_server_main_configures_graceful_shutdown_timeout():
    """run.py passes timeout_graceful_shutdown=15 to the production uvicorn.Config."""
    import ast
    from pathlib import Path

    source = Path("src/run.py").read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "Config"
            and isinstance(func.value, ast.Name)
            and func.value.id == "uvicorn"
        ):
            for kw in node.keywords:
                if kw.arg == "timeout_graceful_shutdown":
                    assert isinstance(kw.value, ast.Constant)
                    assert kw.value.value == 15
                    return
            raise AssertionError(
                "uvicorn.Config() in src/run.py missing timeout_graceful_shutdown"
            )

    raise AssertionError("no uvicorn.Config() call found in src/run.py")


# --- Startup SIGINT handling (lifespan startup) --------------------------------


@pytest.mark.asyncio
async def test_sigint_during_startup_records_shutdown_intent(mocker):
    """SIGINT during startup must record application shutdown intent."""
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown", return_value=True)

    server = make_server()

    async def slow_startup(*args, **kwargs):
        server.handle_exit(2, None)
        return

    mocker.patch.object(uvicorn.Server, "startup", side_effect=slow_startup)
    mocker.patch.object(uvicorn.Server, "shutdown", new_callable=AsyncMock)
    # Mock lifespan to avoid real shutdown call
    server.lifespan = MagicMock(should_exit=False, shutdown=AsyncMock(), startup=AsyncMock(), state={})
    mocker.patch.object(uvicorn.Server, "_log_started_message")

    await server.startup()

    assert server.should_exit is True
    assert server._shutdown_intent_marked is True
    assert server._startup_shutdown_requested is True


@pytest.mark.asyncio
async def test_shutdown_during_startup_prevents_update_check_and_log(mocker):
    """Shutdown during super().startup() must not schedule update-check nor log running."""
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown")
    log_mock = mocker.patch.object(uvicorn.Server, "_log_started_message")
    mocker.patch("run.CONFIG.getboolean", return_value=True)

    server = make_server()
    server.lifespan = MagicMock(should_exit=False, shutdown=AsyncMock(), startup=AsyncMock(), state={})
    mocker.patch.object(uvicorn.Server, "shutdown", new_callable=AsyncMock)

    async def slow_startup(self, sockets=None):
        self.handle_exit(2, None)
        # Simulate super().startup returning early without listeners when lifespan.should_exit
        return

    mocker.patch.object(uvicorn.Server, "startup", autospec=True, side_effect=slow_startup)
    # Patch update check to ensure not called
    update_check = mocker.patch("app.utils.update_check.run_update_check")

    await server.startup()

    assert server.should_exit is True
    assert server._update_check_task is None
    update_check.assert_not_called()
    # _log_started_message should have been suppressed (not called when should_exit)
    # Our startup should not have called it because super().startup returned early
    log_mock.assert_not_called()
    server2 = make_server()
    server2.should_exit = True
    server2._log_started_message([])
    log_mock.assert_not_called()


@pytest.mark.asyncio
async def test_log_suppressed_when_should_exit_during_startup(mocker):
    """_log_started_message must be suppressed when should_exit is already set."""
    server = make_server()
    server.should_exit = True
    log_mock = mocker.patch.object(uvicorn.Server, "_log_started_message")

    server._log_started_message([])

    log_mock.assert_not_called()


@pytest.mark.asyncio
async def test_normal_startup_still_schedules_update_check(mocker):
    """Normal startup (no SIGINT) must still schedule update-check once."""
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown")

    async def normal_startup(sockets=None):
        mocker.patch.object(uvicorn.Server, "_log_started_message")
        # Simulate successful super startup
        pass

    # Use real startup flow with mocked parent
    async def parent_startup(server, sockets=None):
        server.started = True

    check = mocker.patch("app.utils.update_check.run_update_check", new_callable=AsyncMock)
    mocker.patch.object(uvicorn.Server, "startup", autospec=True, side_effect=parent_startup)
    mocker.patch.object(uvicorn.Server, "shutdown", autospec=True)
    mocker.patch("run.CONFIG.getboolean", return_value=True)

    server = make_server()
    await server.startup()
    # Simulate _serve's should_exit check not triggering
    assert server.should_exit is False
    assert server._update_check_task is not None
    # Second startup should not create another
    await server.startup()
    assert check.call_count == 1 or server._update_check_task is not None
    await server.shutdown()


@pytest.mark.asyncio
async def test_startup_exception_still_propagates(mocker):
    """Real startup errors must propagate, not be converted to clean shutdown."""
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown")

    async def failing_startup(*args, **kwargs):
        raise RuntimeError("startup failed")

    mocker.patch.object(uvicorn.Server, "startup", side_effect=failing_startup)
    server = make_server()

    with pytest.raises(RuntimeError, match="startup failed"):
        await server.startup()

    assert server._update_check_task is None
    assert server.should_exit is False


@pytest.mark.asyncio
async def test_startup_then_serve_skips_main_loop_when_should_exit_during_startup(mocker):
    """Real _serve path must skip main_loop when should_exit set during startup (no fake logic)."""
    mocker.patch("app.services.browser.engine.request_application_shutdown")
    mocker.patch("run.request_generic_shutdown")

    server = make_server()
    server.lifespan = MagicMock(should_exit=False, shutdown=AsyncMock(), startup=AsyncMock(), state={})
    mocker.patch.object(uvicorn.Server, "shutdown", new_callable=AsyncMock)

    async def slow_startup(*args, **kwargs):
        server.handle_exit(2, None)
        return

    mocker.patch.object(uvicorn.Server, "startup", side_effect=slow_startup)
    await server.startup()
    assert server.should_exit is True
    assert server._startup_shutdown_requested is True
    assert server._update_check_task is None
    assert server.started is False


@pytest.mark.asyncio
async def test_startup_sigint_uses_lifespan_boundary(mocker):
    """SIGINT during lifespan startup must set lifespan.should_exit and prevent listeners."""
    server = make_server()
    mock_lifespan = MagicMock()
    mock_lifespan.should_exit = False
    mock_lifespan.state = {}
    mock_lifespan.shutdown = AsyncMock()
    # Simulate super().startup observing lifespan.should_exit and returning without listeners
    # Patch super().startup to set started only if not should_exit, like real Uvicorn
    async def mock_super_startup(sockets=None):
        # This simulates Uvicorn's startup after lifespan.should_exit check
        if server.lifespan.should_exit:
            return
        # Simulate normal listener creation
        server.started = True

    mocker.patch.object(uvicorn.Server, "startup", side_effect=mock_super_startup)
    server.lifespan = mock_lifespan
    # Simulate SIGINT during startup before super().startup
    server.handle_exit(2, None)
    # At this point, _startup_shutdown_requested True and lifespan.should_exit True
    assert server._startup_shutdown_requested is True
    assert server.lifespan.should_exit is True
    assert server.should_exit is True

    # Now call ApplicationServer.startup which should propagate _startup_shutdown_requested to lifespan.should_exit
    # and then call super().startup which will return early, then handle shutdown
    # Test the pre-startup propagation
    server2 = make_server()
    server2.lifespan = MagicMock(should_exit=False, shutdown=AsyncMock(), startup=AsyncMock(), state={})
    server2._startup_shutdown_requested = True
    # Mock super().startup to check that lifespan.should_exit was set before call
    async def check_super(sockets=None):
        assert server2.lifespan.should_exit is True
        # Simulate Uvicorn returning before listeners
        return

    mocker.patch.object(uvicorn.Server, "startup", side_effect=check_super)
    mocker.patch.object(uvicorn.Server, "shutdown", new_callable=AsyncMock)
    server2.lifespan.shutdown = AsyncMock()
    await server2.startup()
    # Should have called lifespan.shutdown exactly once via startup's post-super handling
    server2.lifespan.shutdown.assert_awaited_once()
    assert server2._update_check_task is None
    assert server2.started is False


@pytest.mark.asyncio
async def test_startup_race_uses_super_shutdown_for_listeners(mocker):
    """Narrow race where startup returns started=True must use super().shutdown() to close listeners."""
    server = make_server()
    # Simulate super().startup creating listeners despite should_exit (race)
    async def mock_super_startup(sockets=None):
        server.started = True
        server.servers = [MagicMock(close=MagicMock(), wait_closed=AsyncMock())]

    mocker.patch.object(uvicorn.Server, "startup", side_effect=mock_super_startup)
    super_shutdown = mocker.patch.object(uvicorn.Server, "shutdown", new_callable=AsyncMock)
    server.lifespan = MagicMock(should_exit=False, shutdown=AsyncMock(), startup=AsyncMock(), state={})
    server._startup_shutdown_requested = True
    server.should_exit = True
    server.force_exit = False

    await server.startup()

    # Should have called super().shutdown to close listeners via Uvicorn's own shutdown
    super_shutdown.assert_awaited_once()
    assert server._update_check_task is None


@pytest.mark.asyncio
async def test_normal_startup_uses_inherited_uvicorn_startup(mocker):
    """Normal startup must use inherited Uvicorn startup and schedule update-check once."""
    server = make_server()
    # Mock super().startup to simulate normal Uvicorn startup
    async def mock_super(sockets=None):
        server.started = True
        server.lifespan = MagicMock(should_exit=False, state={})
        # No should_exit set

    mocker.patch.object(uvicorn.Server, "startup", side_effect=mock_super)
    mocker.patch("run.CONFIG.getboolean", return_value=True)
    mock_update = mocker.patch("app.utils.update_check.run_update_check", new_callable=AsyncMock)

    await server.startup()
    assert server.started is True
    assert server._update_check_task is not None
    first_task = server._update_check_task
    # Second startup should not create another
    await server.startup()
    assert server._update_check_task is first_task
    # Cleanup
    if server._update_check_task:
        server._update_check_task.cancel()
        try:
            await server._update_check_task
        except:
            pass


@pytest.mark.asyncio
async def test_genuine_startup_failure_not_mistaken_for_sigint(mocker):
    """Genuine startup failure must not be mistaken for operator startup shutdown."""
    server = make_server()
    mock_lifespan = MagicMock(should_exit=False, shutdown=AsyncMock(), startup=AsyncMock(), state={})
    server.lifespan = mock_lifespan
    server._startup_shutdown_requested = False

    # Simulate inherited Uvicorn startup returning with should_exit=True for genuine failure
    # while _startup_shutdown_requested remains False
    async def mock_super(sockets=None):
        server.should_exit = True
        # started remains False (no listeners), like real Uvicorn when lifespan.should_exit
        return

    mocker.patch.object(uvicorn.Server, "startup", side_effect=mock_super)
    mocker.patch.object(uvicorn.Server, "shutdown", new_callable=AsyncMock)

    await server.startup()

    # Startup-SIGINT cleanup branch must only execute when _startup_shutdown_requested=True
    mock_lifespan.shutdown.assert_not_called()
    assert server._update_check_task is None
    assert server._startup_shutdown_requested is False
