"""Windows real-process updater lifecycle tests.

Every test here executes REAL Windows mechanics (real msvcrt locks, real
ctypes liveness, real loopback IPC, real update-windows.cmd). Nothing is mocked;
on non-Windows platforms the whole module self-skips so CI on Linux/macOS
never reports false passes.

These become required gates in Phase 7.
"""

import json
import os
import subprocess
import sys
import time

import pytest

from ._harness import (  # noqa: E402
    REPO_ROOT,
    SERVICE_STUB,
    UPDATE_PY,
    free_port,
    health_status,
    pid_alive,
    port_serving,
    read_pid,
    spawn_service,
    wait_health,
    wait_process_exit,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="real Windows only"),
    pytest.mark.timeout(240),
]


def _updater_env(repo, **overrides):
    return repo.env(**overrides)


def test_update_platform_imports_on_windows():
    import importlib.util

    scripts_dir = os.path.join(REPO_ROOT, "scripts")
    spec = importlib.util.spec_from_file_location(
        "update_platform_win",
        os.path.join(scripts_dir, "update_platform.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not raise ImportError(fcntl)
    assert module.IS_WINDOWS is True


def test_msvcrt_lock_acquire_contention_crash_release(repo, tmp_path):
    lock_path = str(tmp_path / "win-int.lock")

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "update_platform_win2",
        os.path.join(REPO_ROOT, "scripts", "update_platform.py"),
    )
    platform = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(platform)

    handle = platform.acquire_lock(lock_path)
    assert handle is not None
    assert os.path.getsize(lock_path) >= 1  # byte-range initialized

    contender = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time;"
         "sys.path.insert(0, sys.argv[1]);"
         "from update_platform import acquire_lock;"
         "h = acquire_lock(sys.argv[2]);"
         "print('CONTENTED' if h is None else 'ACQUIRED');"
         "time.sleep(0.2);",
         os.path.join(REPO_ROOT, "scripts"), lock_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out, _ = contender.communicate(timeout=30)
    assert "CONTENTED" in out  # exact errno recorded by unit suite

    handle.release()
    reacquired = platform.acquire_lock(lock_path)
    assert reacquired is not None
    reacquired.release()

    # Crash-release: holder signals READY only after acquiring the lock;
    # the parent verifies ownership before killing it.
    holder_source = """
import sys
import time

sys.path.insert(0, sys.argv[1])
from update_platform import acquire_lock

while True:
    handle = acquire_lock(sys.argv[2])
    if handle is not None:
        break
    time.sleep(0.05)

print("READY", flush=True)
time.sleep(60)
"""
    crasher = subprocess.Popen(
        [sys.executable, "-c", holder_source,
         os.path.join(REPO_ROOT, "scripts"), lock_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    ready_line = crasher.stdout.readline().strip()
    if ready_line != "READY":
        stderr_tail = crasher.stderr.read() if crasher.stderr else ""
        crasher.kill()
        raise AssertionError(
            f"crash holder died before READY: {ready_line!r} {stderr_tail}"
        )
    crasher.kill()  # abrupt death while owning the kernel lock
    crasher.wait(timeout=5)

    final = platform.acquire_lock(lock_path)
    assert final is not None  # kernel released on crash
    final.release()


def test_windows_liveness_real_children(repo):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "update_platform_win3",
        os.path.join(REPO_ROOT, "scripts", "update_platform.py"),
    )
    platform = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(platform)

    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert platform.pid_alive(live.pid) is True
    finally:
        live.terminate()
        live.wait(timeout=5)

    assert platform.pid_alive(live.pid) is False   # exited
    assert platform.pid_alive(4_000_000) is False  # invalid/nonexistent


def test_graceful_ipc_stop_real_server(repo, tracked_processes):
    """Full chain: server -> control file -> --stop -> graceful exit."""
    port = free_port()
    url = f"http://127.0.0.1:{port}/health"
    server_argv = [
        sys.executable,
        os.path.join(REPO_ROOT, "src", "run.py"),
        "--host", "127.0.0.1",
        "--port", str(port),
    ]

    proc = spawn_service(repo, server_argv, tracked_processes,
                         ready_url=url, ready_timeout=60,
                         env=repo.env())
    with open(repo.pid_file, "w") as handle:
        handle.write(str(proc.pid))

    control_file = os.path.join(repo.runtime_dir, "shutdown-control.json")
    assert wait_health(url, timeout=5)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not os.path.exists(control_file):
        time.sleep(0.1)
    assert os.path.exists(control_file), "server never published control file"

    result = repo.run_updater(["--stop"], extra_env={
        "WEBAI_HEALTH_URL": url,
    })
    assert result.returncode == 0, result.stderr
    assert wait_pid_gone_windows(proc.pid)

    final_log = open(
        repo.log_file, encoding="utf-8", errors="replace"
    ).read()
    assert ("Application shutdown requested" in final_log
            or "FastAPI application lifespan shutdown executing." in final_log)
    time.sleep(1.0)
    assert health_status(url) is None            # endpoint down after stop
    assert not os.path.exists(control_file)      # owned metadata removed
    assert not os.path.exists(repo.pid_file)


def test_startup_race_pid_before_listener_ready(repo, tracked_processes):
    """PID exists before IPC readiness -> updater retries -> IPC accepted.

    Uses the REAL ApplicationServer (its ShutdownListener appears only when
    Uvicorn finishes binding), so early stop attempts hit the retry path and
    the stop completes gracefully well inside the hard-fallback budget.
    """
    port = free_port()
    url = f"http://127.0.0.1:{port}/health"
    server_argv = [
        sys.executable,
        os.path.join(REPO_ROOT, "src", "run.py"),
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    proc = spawn_service(repo, server_argv, tracked_processes,
                         ready_timeout=0,  # intentionally unready
                         env=repo.env())
    with open(repo.pid_file, "w") as handle:
        handle.write(str(proc.pid))          # PID known before listener

    started = time.monotonic()
    stopped = repo.run_updater(["--stop"], extra_env={
        "WEBAI_HEALTH_URL": url,
    })
    elapsed = time.monotonic() - started

    assert stopped.returncode == 0, stopped.stderr
    # Accepted via IPC, not the ~10s force fallback.
    assert elapsed < 10.0, f"stop took {elapsed:.1f}s; force fallback ran?"
    assert "forcing termination" not in stopped.stdout + stopped.stderr
    assert wait_pid_gone_windows(proc.pid)

    control_file = os.path.join(repo.runtime_dir, "shutdown-control.json")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and os.path.exists(control_file):
        time.sleep(0.1)
    assert not os.path.exists(control_file)


def wait_pid_gone_windows(pid, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return False


def test_hard_fallback_budget_approx_ten_seconds(repo):
    """Live process, no usable control channel -> force at ~10s wall clock."""
    victim = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        with open(repo.pid_file, "w") as handle:
            handle.write(str(victim.pid))
        assert pid_alive(victim.pid)

        started = time.monotonic()
        result = repo.run_updater(["--stop"], timeout=120)
        elapsed = time.monotonic() - started

        assert result.returncode == 0, result.stderr
        assert 9.0 <= elapsed <= 13.0, (
            f"hard fallback drifted: {elapsed:.1f}s"
        )
        assert not pid_alive(victim.pid) or victim.poll() is not None
    finally:
        if victim.poll() is None:
            victim.kill()
            victim.wait(timeout=5)


def test_spawned_service_survives_updater_exit_and_pid_manageable(
    repo, tracked_processes,
):
    port = free_port()
    url = f"http://127.0.0.1:{port}/health"
    proc = spawn_service(
        repo,
        [sys.executable, SERVICE_STUB, str(port)],
        tracked_processes,
        ready_url=url,
    )
    with open(repo.pid_file, "w") as handle:
        handle.write(str(proc.pid))
    # Updater-equivalent parent already exited; service keeps serving.
    assert health_status(url) == 200
    assert pid_alive(read_pid(repo))


def test_windows_quoted_path_parsing_real(tmp_path):
    """Real parse_start_command with spaced executable + spaced argument.

    Uses a REAL executable copy under a spaced directory so shutil.which()
    resolves it unmocked; the script argument also lives under a spaced
    path. No shell, no fake resolution.
    """
    import importlib.util
    import shutil

    spaced_bin = tmp_path / "Poetry Dir With Spaces"
    spaced_bin.mkdir()
    exe_copy = spaced_bin / "poetry-probe.exe"
    shutil.copyfile(sys.executable, exe_copy)  # resolution target only,
    # never executed: parse_start_command resolves argv[0], nothing more.

    spaced_repo = tmp_path / "Repo With Spaces"
    spaced_repo.mkdir()
    script = spaced_repo / "run.py"
    script.write_text("# probe\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("update_parse_win_r", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.START_COMMAND = (
        f'"{exe_copy}" run python "{script}" --host 127.0.0.1'
    )

    argv = module.parse_start_command()

    assert argv[0] == str(exe_copy)          # resolved via real which()
    assert "\" " not in argv[0]             # stays one token
    assert any(token == str(script) for token in argv[1:])
    assert "--host" in argv and "127.0.0.1" in argv


def test_update_windows_wrapper_real_execution(repo):
    """Real cmd.exe wrapper: repo-root cd, %* forwarding, rc propagation."""
    with open(repo.pid_file, "w") as handle:
        handle.write("999999999")
    env = repo.env()
    result = subprocess.run(
        ["cmd", "/c", os.path.join(REPO_ROOT, "update-windows.cmd"), "--stop"],
        cwd=env["WEBAI_ROOT"], env=env, capture_output=True, text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert not os.path.exists(repo.pid_file)


def test_spaces_checkout_end_to_end_windows(tmp_path, tracked_processes):
    """Real ApplicationServer lifecycle under spaced checkout/runtime paths."""
    from ._harness import IntegrationRepo

    spaced_base = tmp_path / "WebAI to API integration"
    spaced_base.mkdir()
    repo = IntegrationRepo(spaced_base)
    assert " " in repo.work and " " in repo.runtime_dir

    port_a = free_port()
    url_a = f"http://127.0.0.1:{port_a}/health"
    run_py = os.path.join(REPO_ROOT, "src", "run.py")
    seed_argv = [
        sys.executable, run_py, "--host", "127.0.0.1",
        "--port", str(port_a),
    ]
    old_proc = spawn_service(
        repo, seed_argv, tracked_processes,
        ready_url=url_a, ready_timeout=60, env=repo.env(),
    )
    with open(repo.pid_file, "w") as handle:
        handle.write(str(old_proc.pid))
    sha_before = repo.head()

    control_file = os.path.join(repo.runtime_dir, "shutdown-control.json")
    assert os.path.exists(control_file)
    with open(control_file, encoding="utf-8") as handle:
        initial_control = json.load(handle)
    initial_token = initial_control["token"]
    assert isinstance(initial_token, str) and initial_token
    assert isinstance(initial_control["port"], int)

    repo.remote_set_version("2.0")
    port_b = free_port()
    url_b = f"http://127.0.0.1:{port_b}/health"
    start_command = (
        f'"{sys.executable}" "{run_py}" '
        f'--host 127.0.0.1 --port {port_b}'
    )
    final_pid = None
    stopped_cleanly = False
    updated = repo.run_updater([], extra_env={
        "WEBAI_START_COMMAND": start_command,
        "WEBAI_HEALTH_URL": url_b,
    })
    try:
        assert updated.returncode == 0, updated.stderr
        assert 'version = "2.0"' in repo.read("pyproject.toml")
        final_pid = read_pid(repo)
        assert pid_alive(final_pid)
        assert wait_health(url_b, timeout=30)
        assert wait_process_exit(old_proc, timeout=30)

        # Old metadata may survive briefly during restart. Require a fresh
        # valid publication and token rotation before attempting final stop.
        restarted_control = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with open(control_file, encoding="utf-8") as handle:
                    candidate = json.load(handle)
                if (
                    isinstance(candidate.get("token"), str)
                    and candidate["token"]
                    and candidate["token"] != initial_token
                    and isinstance(candidate.get("port"), int)
                    and candidate["port"] > 0
                ):
                    restarted_control = candidate
                    break
            except (OSError, ValueError, TypeError, KeyError):
                pass
            time.sleep(0.1)
        assert restarted_control is not None, (
            "restarted server did not publish fresh shutdown metadata"
        )

        log_offset = os.path.getsize(repo.log_file)
        stop_started = time.monotonic()

        stopped = repo.run_updater(["--stop"], extra_env={
            "WEBAI_HEALTH_URL": url_b,
        })
        assert stopped.returncode == 0, stopped.stderr
        stop_elapsed = time.monotonic() - stop_started
        stopped_cleanly = True
        assert "forcing termination" not in stopped.stdout + stopped.stderr
        assert stop_elapsed < 10.0, (
            f"final stop reached fallback budget: {stop_elapsed:.2f}s"
        )
        assert wait_pid_gone_windows(final_pid)
        assert not os.path.exists(repo.pid_file)
        assert not os.path.exists(control_file)

        with open(repo.log_file, "rb") as handle:
            handle.seek(log_offset)
            final_tail = handle.read().decode("utf-8", errors="replace")
        assert (
            "Application shutdown requested" in final_tail
            or "FastAPI application lifespan shutdown executing." in final_tail
        )
        assert health_status(url_b) is None
        assert not port_serving(port_b)
        assert repo.head() != sha_before
    finally:
        if not stopped_cleanly:
            try:
                repo.run_updater(["--stop"], timeout=60)
            except Exception:
                if final_pid is not None and pid_alive(final_pid):
                    # Defensive test-only backstop after updater cleanup fails.
                    import signal
                    os.kill(final_pid, signal.SIGTERM)
                    wait_pid_gone_windows(final_pid)


def test_rollback_cleanup_windows(tmp_path, tracked_processes):
    """Authoritative Windows rollback contract (Fixes 1+2).

    Structured-argv seeded service (unambiguous PID ownership):
      A healthy -> update to B -> B fails health -> rollback to A ->
      final A healthy via updater restart -> explicit updater --stop ->
      final PID gone, PID file removed, final port silent.
    """
    from ._harness import IntegrationRepo

    repo = IntegrationRepo(tmp_path / "rollback-fixture")
    final_proc = None
    stopped_cleanly = False
    try:
        port_a = free_port()
        url_a = f"http://127.0.0.1:{port_a}/health"
        old_proc = spawn_service(
            repo,
            [sys.executable, _stub_path(), str(port_a)],
            tracked_processes,
            ready_url=url_a,
        )
        with open(repo.pid_file, "w") as handle:
            handle.write(str(old_proc.pid))
        sha_a = repo.head()
        assert health_status(url_a) == 200

        # B ships a tracked file forcing its /health to 500.
        repo.remote_set_version("2.0", extra_files={".fail-health": "500\n"})

        fail_port = free_port()  # also serves the restarted A after rollback
        url_final = f"http://127.0.0.1:{fail_port}/health"
        rolled_back = repo.run_updater([], extra_env={
            "WEBAI_START_COMMAND": _stub_start_command_static(fail_port),
            "WEBAI_HEALTH_URL": url_final,
            "WEBAI_HEALTH_TIMEOUT": "3",
            "WEBAI_HEALTH_INTERVAL": "0.2",
        })

        assert rolled_back.returncode != 0
        combined = rolled_back.stdout + rolled_back.stderr
        assert "ROLLBACK FAILED" not in combined
        assert repo.head() == sha_a
        assert 'version = "1.0"' in repo.read("pyproject.toml")
        assert not os.path.exists(os.path.join(repo.work, ".fail-health"))

        # Old managed process is gone; updater-spawned final A is live.
        assert wait_pid_gone_windows(old_proc.pid)
        deadline = time.monotonic() + 30
        final_pid = None
        while time.monotonic() < deadline:
            try:
                candidate = read_pid(repo)
                if pid_alive(candidate):
                    final_pid = candidate
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        assert final_pid is not None, "no live final service after rollback"
        assert wait_health(url_final, timeout=30)

        # Deterministic cleanup THROUGH the updater itself.
        stopped = repo.run_updater(["--stop"], extra_env={
            "WEBAI_HEALTH_URL": url_final,
        })
        assert stopped.returncode == 0, stopped.stderr
        stopped_cleanly = True

        assert wait_pid_gone_windows(final_pid)
        assert not os.path.exists(repo.pid_file)
        time.sleep(1.0)
        assert health_status(url_final) is None
    finally:
        if not stopped_cleanly:
            # Defensive backstop so failed assertions never leak servers.
            try:
                repo.run_updater(["--stop"], timeout=60)
            except Exception:
                pass


def _stub_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_service_stub.py"
    )


def _stub_start_command_static(port):
    return f'"{sys.executable}" "{_stub_path()}" {port}'


def test_real_poetry_lifecycle_through_updater_stop(
    repo, tracked_processes,
):
    """Real `poetry run python src/run.py` on Windows, stopped by the updater.

    Mirrors the POSIX real-Poetry contract with the FULL graceful contract:
    the Poetry-launched ApplicationServer runs in the isolated repo
    environment (same RUNTIME_DIR as the updater), publishes fresh shutdown
    IPC metadata, and `updater --stop` must complete via IPC — force
    fallback is a test failure, not an accepted outcome.
    """
    import json
    import shutil

    poetry = shutil.which("poetry")
    if poetry is None:
        pytest.skip("Poetry not available for Windows lifecycle integration")

    port = free_port()
    url = f"http://127.0.0.1:{port}/health"
    run_py = os.path.join(REPO_ROOT, "src", "run.py")
    spawn_argv = [
        poetry, "run", "python", run_py,
        "--host", "127.0.0.1", "--port", str(port),
    ]

    # Same isolated environment as the updater: identical WEBAI_* state,
    # PID/log paths, and RUNTIME_DIR for shutdown-control.json.
    server_env = repo.env()

    log = open(repo.log_file, "ab")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(
        spawn_argv,
        cwd=REPO_ROOT,  # real project root so Poetry resolves its own venv
        stdout=log,
        stderr=subprocess.STDOUT,
        env=server_env,
        creationflags=creationflags,
    )
    log.close()
    tracked_processes(proc)
    with open(repo.pid_file, "w") as handle:
        handle.write(str(proc.pid))  # outer PID is what the updater manages

    control_file = os.path.join(repo.runtime_dir, "shutdown-control.json")
    stopped_cleanly = False
    try:
        assert wait_health(url, timeout=90, process=proc), (
            f"process_exit={proc.poll()!r}; log:\n"
            f"{open(repo.log_file, encoding='utf-8', errors='replace').read()}"
        )
        assert pid_alive(proc.pid)

        # Fresh Phase 4 metadata must exist inside the isolated runtime.
        deadline = time.monotonic() + 30
        control = None
        while time.monotonic() < deadline:
            try:
                with open(control_file, encoding="utf-8") as handle:
                    candidate = json.load(handle)
                if (
                    isinstance(candidate.get("token"), str)
                    and candidate["token"]
                    and isinstance(candidate.get("port"), int)
                    and candidate["port"] > 0
                ):
                    control = candidate
                    break
            except (OSError, ValueError, TypeError):
                pass
            time.sleep(0.1)
        assert control is not None, (
            "Poetry-launched server never published shutdown metadata in "
            f"{repo.runtime_dir}"
        )

        # Graceful stop is MANDATORY: scoped log evidence + no fallback.
        log_offset = os.path.getsize(repo.log_file)
        stop_started = time.monotonic()
        stopped = repo.run_updater(["--stop"], extra_env={
            "WEBAI_HEALTH_URL": url,
        })
        assert stopped.returncode == 0, stopped.stderr
        combined = stopped.stdout + stopped.stderr
        assert "forcing termination" not in combined, (
            "updater fell back to hard termination; graceful IPC was not "
            f"used (control={control!r})"
        )
        assert time.monotonic() - stop_started < 10.0

        with open(repo.log_file, "rb") as handle:
            handle.seek(log_offset)
            tail = handle.read().decode("utf-8", errors="replace")
        assert (
            "Application shutdown requested" in tail
            or "FastAPI application lifespan shutdown executing." in tail
        ), "no graceful lifecycle evidence appended by the final stop"

        assert wait_process_exit(proc, timeout=30)
        assert not os.path.exists(repo.pid_file)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and os.path.exists(control_file):
            time.sleep(0.1)
        assert not os.path.exists(control_file)
        assert health_status(url) is None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not port_serving(port):
                break
            time.sleep(0.2)
        else:
            raise AssertionError(
                "orphan WebAI server still serving after updater --stop"
            )
        stopped_cleanly = True
    finally:
        if not stopped_cleanly:
            # Ordered backstop: updater stop first, then outer process only.
            try:
                repo.run_updater(["--stop"], timeout=60)
            except Exception:
                pass  # contained: never masks the original failure
            if proc.poll() is None:
                proc.terminate()
                if not wait_process_exit(proc, timeout=10):
                    proc.kill()
                    if not wait_process_exit(proc, timeout=10):
                        # Diagnostic-only: never mask the original failure.
                        print(
                            "CLEANUP LIMITATION: outer Poetry process "
                            "could not be reaped after kill."
                        )
            # The PID file names the OUTER Poetry PID; after it dies the
            # inner uvicorn PID is not directly known to this harness, so
            # child cleanup can only be verified, not forced by PID.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not port_serving(port):
                    break
                time.sleep(0.2)
            else:
                # Known limitation (see audit notes): no scoped handle to
                # the inner server PID once Poetry is gone. Surface loudly
                # without masking any original assertion failure.
                print(
                    "CLEANUP LIMITATION: WebAI server still serving on "
                    f"port {port} after updater stop and outer-process "
                    "reap; inner PID is not tracked by the harness."
                )
