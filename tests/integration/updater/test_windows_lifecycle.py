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
    UPDATE_PY,
    _production_platform,
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


def _disable_background_update_check(root, request):
    """Keep a server's startup update check from contending with its stop."""
    path = os.path.join(root, "config.conf")
    try:
        with open(path, "rb") as handle:
            original = handle.read()
    except FileNotFoundError:
        original = None
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("[General]\ncheck_updates = false\n")

    def restore():
        if original is None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        else:
            with open(path, "wb") as handle:
                handle.write(original)

    request.addfinalizer(restore)


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
    ready_path = str(tmp_path / "crasher.ready")

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "update_platform_win2",
        os.path.join(REPO_ROOT, "scripts", "update_platform.py"),
    )
    platform = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(platform)

    handle = platform.acquire_lock(lock_path)
    contender = None
    crasher = None

    def reap(process):
        if process.poll() is None:
            process.kill()
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.communicate(timeout=5)

    try:
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
        try:
            out, err = contender.communicate(timeout=30)
        except subprocess.TimeoutExpired as error:
            out, err = reap(contender)
            raise AssertionError(
                f"contender did not exit: stdout={out!r} stderr={err!r}"
            ) from error
        assert "CONTENTED" in out, (
            f"contender output={out!r} stderr={err!r}"
        )

        handle.release()
        handle = None
        reacquired = platform.acquire_lock(lock_path)
        try:
            assert reacquired is not None
        finally:
            if reacquired is not None:
                reacquired.release()

        # Crash-release: holder signals readiness through a file only after
        # acquiring the lock; the parent uses bounded process-aware polling.
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

with open(sys.argv[3], "w", encoding="ascii") as ready:
    ready.write("READY")
    ready.flush()
time.sleep(60)
"""
        crasher = subprocess.Popen(
            [sys.executable, "-c", holder_source,
             os.path.join(REPO_ROOT, "scripts"), lock_path, ready_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        ready_deadline = time.monotonic() + 30
        while time.monotonic() < ready_deadline:
            if os.path.exists(ready_path):
                break
            if crasher.poll() is not None:
                out, err = reap(crasher)
                raise AssertionError(
                    "crash holder exited before READY: "
                    f"returncode={crasher.returncode!r} "
                    f"stdout={out!r} stderr={err!r}"
                )
            remaining = ready_deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.05, remaining))
        else:
            out, err = reap(crasher)
            raise AssertionError(
                "crash holder did not become READY: "
                f"returncode={crasher.returncode!r} "
                f"stdout={out!r} stderr={err!r}"
            )

        crasher.kill()  # abrupt death while owning the kernel lock
        out, err = reap(crasher)
        assert crasher.returncode is not None, (
            f"crash holder was not reaped: stdout={out!r} stderr={err!r}"
        )

        final = platform.acquire_lock(lock_path)
        try:
            assert final is not None  # kernel released on crash
        finally:
            if final is not None:
                final.release()
    finally:
        if handle is not None:
            handle.release()
        for process in (contender, crasher):
            if process is not None and process.poll() is None:
                try:
                    reap(process)
                except subprocess.TimeoutExpired:
                    pass


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


def test_graceful_ipc_stop_real_server(repo, tracked_processes, request):
    """Full chain: server -> control file -> --stop -> graceful exit."""
    from app.shutdown_transport import identify_server

    port = free_port()
    url = f"http://127.0.0.1:{port}/health"
    server_argv = [
        sys.executable,
        os.path.join(REPO_ROOT, "src", "run.py"),
        "--host", "127.0.0.1",
        "--port", str(port),
    ]

    _disable_background_update_check(repo.work, request)
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

    with open(control_file, encoding="utf-8") as handle:
        control = json.load(handle)
    assert isinstance(control.get("port"), int) and control["port"] > 0
    assert isinstance(control.get("token"), str) and control["token"]
    assert isinstance(control.get("pid"), int) and not isinstance(
        control["pid"], bool
    ) and control["pid"] > 0
    launcher_pid = proc.pid
    metadata_pid = control["pid"]
    identify_pid = identify_server(control_file)
    pid_file_before = read_pid(repo)
    assert identify_pid == metadata_pid
    assert pid_alive(metadata_pid)

    stopped_cleanly = False
    try:
        result = repo.run_updater(["--stop"], extra_env={
            "WEBAI_HEALTH_URL": url,
        })

        def diagnostics():
            return (
                f"launcher_pid={launcher_pid}; metadata_pid={metadata_pid}; "
                f"identify_pid={identify_pid}; pid_file_before={pid_file_before}; "
                f"returncode={result.returncode}; stdout={result.stdout!r}; "
                f"stderr={result.stderr!r}; proc.poll()={proc.poll()!r}; "
                f"launcher_alive={pid_alive(launcher_pid)}; "
                f"metadata_alive={pid_alive(metadata_pid)}; "
                f"control_exists={os.path.exists(control_file)}; "
                f"health={health_status(url)!r}; port_serving={port_serving(port)}"
            )

        assert result.returncode == 0, diagnostics()
        assert wait_pid_gone_windows(metadata_pid), diagnostics()
        assert not os.path.exists(repo.pid_file), diagnostics()
        assert not os.path.exists(control_file), diagnostics()
        assert health_status(url) is None, diagnostics()
        assert not port_serving(port), diagnostics()

        if launcher_pid == metadata_pid:
            assert wait_process_exit(proc), diagnostics()
        else:
            assert wait_process_exit(proc), (
                "Windows launcher/wrapper remained alive after authoritative "
                f"WebAI server exit; {diagnostics()}"
            )

        final_log = open(
            repo.log_file, encoding="utf-8", errors="replace"
        ).read()
        assert (
            "Application shutdown requested" in final_log
            or "FastAPI application lifespan shutdown executing." in final_log
        ), diagnostics()
        stopped_cleanly = True
    finally:
        if not stopped_cleanly and proc.poll() is None:
            proc.terminate()
            if not wait_process_exit(proc, timeout=10):
                proc.kill()
                wait_process_exit(proc, timeout=10)
        if not stopped_cleanly and pid_alive(metadata_pid):
            _production_platform().force_kill(metadata_pid)
            wait_pid_gone_windows(metadata_pid)


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


def test_spaces_checkout_end_to_end_windows(tmp_path, tracked_processes, request):
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
    _disable_background_update_check(repo.work, request)
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


def test_real_poetry_lifecycle_through_updater_stop(
    repo, tracked_processes, monkeypatch, request,
):
    """Real `poetry run python src/run.py` on Windows, stopped by the updater.

    Mirrors the POSIX real-Poetry contract with the FULL graceful contract:
    the Poetry-launched ApplicationServer runs in the isolated repo
    environment (same RUNTIME_DIR as the updater), publishes fresh shutdown
    IPC metadata, and `updater --stop` must complete via IPC — force
    fallback is a test failure, not an accepted outcome.
    """
    import importlib.util
    import json
    import shutil
    from app.shutdown_transport import identify_server

    poetry = shutil.which("poetry")
    if poetry is None:
        pytest.skip("Poetry not available for Windows lifecycle integration")

    port = free_port()
    url = f"http://127.0.0.1:{port}/health"
    run_py = os.path.join(REPO_ROOT, "src", "run.py")
    control_file = os.path.join(repo.runtime_dir, "shutdown-control.json")
    server_env = repo.env()
    for key in (
        "WEBAI_ROOT",
        "WEBAI_PID_FILE",
        "WEBAI_LOG_FILE",
        "WEBAI_LOCK_FILE",
        "RUNTIME_DIR",
        "WEBAI_HEALTH_TIMEOUT",
        "WEBAI_HEALTH_INTERVAL",
    ):
        monkeypatch.setenv(key, server_env[key])
    spec = importlib.util.spec_from_file_location("update_real_poetry", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = REPO_ROOT
    module.PID_FILE = repo.pid_file
    module.LOG_FILE = repo.log_file
    module.LOCK_FILE = repo.lock_file
    module.SHUTDOWN_CONTROL_FILE = control_file
    module.START_COMMAND = (
        f'"{poetry}" run python "{run_py}" '
        f'--host 127.0.0.1 --port {port}'
    )
    _disable_background_update_check(REPO_ROOT, request)

    proc = None
    authoritative_pid = None
    stopped_cleanly = False
    try:
        proc = module.start_service()
        tracked_processes(proc)
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
                    and isinstance(candidate.get("pid"), int)
                    and not isinstance(candidate["pid"], bool)
                    and candidate["pid"] > 0
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
        authoritative_pid = control["pid"]
        assert identify_server(control_file) == authoritative_pid
        assert pid_alive(authoritative_pid)
        assert read_pid(repo) == authoritative_pid

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

        assert wait_pid_gone_windows(authoritative_pid), (
            f"authoritative WebAI server remained alive after updater stop; "
            f"authoritative_pid={authoritative_pid}; proc.poll()={proc.poll()!r}; "
            f"stdout={stopped.stdout!r}; stderr={stopped.stderr!r}"
        )
        assert wait_process_exit(proc, timeout=30), (
            "Poetry launcher remained alive after authoritative WebAI server "
            f"exit; authoritative_pid={authoritative_pid}; "
            f"proc.pid={proc.pid}; proc.poll()={proc.poll()!r}; "
            f"authoritative_alive={pid_alive(authoritative_pid)}; "
            f"stdout={stopped.stdout!r}; stderr={stopped.stderr!r}"
        )

        with open(repo.log_file, "rb") as handle:
            handle.seek(log_offset)
            tail = handle.read().decode("utf-8", errors="replace")
        assert (
            "Application shutdown requested" in tail
            or "FastAPI application lifespan shutdown executing." in tail
        ), "no graceful lifecycle evidence appended after confirmed exit"

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
            if proc is not None and proc.poll() is None:
                proc.terminate()
                if not wait_process_exit(proc, timeout=10):
                    proc.kill()
                    if not wait_process_exit(proc, timeout=10):
                        # Diagnostic-only: never mask the original failure.
                        print(
                            "CLEANUP LIMITATION: outer Poetry process "
                            "could not be reaped after kill."
                        )
            if authoritative_pid is None:
                try:
                    with open(control_file, encoding="utf-8") as handle:
                        candidate = json.load(handle)
                    candidate_pid = candidate.get("pid")
                    if isinstance(candidate_pid, int) and not isinstance(
                        candidate_pid, bool
                    ) and candidate_pid > 0:
                        authoritative_pid = candidate_pid
                except (OSError, ValueError, TypeError):
                    pass
            if authoritative_pid is not None and pid_alive(authoritative_pid):
                try:
                    _production_platform().force_kill(authoritative_pid)
                    assert wait_pid_gone_windows(authoritative_pid)
                except Exception as error:
                    print(
                        "CLEANUP LIMITATION: authoritative PID force-stop "
                        f"failed: {error}"
                    )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not port_serving(port):
                    break
                time.sleep(0.2)
            else:
                print(
                    "CLEANUP LIMITATION: WebAI server still serving on "
                    f"port {port} after updater stop and outer-process "
                    "reap; authoritative PID cleanup failed."
                )
