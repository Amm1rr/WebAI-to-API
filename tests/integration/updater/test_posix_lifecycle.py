"""POSIX (Linux + macOS) real-process updater lifecycle tests.

Runs on Linux today; the same file executes on macOS in Phase 7. No
Linux-only /proc expectations are asserted here.
"""

import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

pytestmark = [
    pytest.mark.skipif(
        sys.platform not in ("linux", "darwin"),
        reason="POSIX real-process lifecycle",
    ),
    pytest.mark.timeout(180),
]

from ._harness import (  # noqa: E402
    UPDATE_PY,
    free_port,
    health_status,
    pid_alive,
    port_serving,
    read_pid,
    spawn_service,
    stub_start_command,
    wait_health,
    wait_process_exit,
    wait_pid_gone,
)

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


def _start_managed_service(repo, tracked, command, port):
    """Start service like the updater would, then record its PID file.

    The updater only starts/stops services around an actual update, so
    lifecycle tests seed a running managed service first.
    """
    from ._harness import spawn_service

    url = f"http://127.0.0.1:{port}/health"
    proc = spawn_service(
        repo, ["/bin/sh", "-c", command], tracked, ready_url=url,
    )
    with open(repo.pid_file, "w") as handle:
        handle.write(str(proc.pid))
    return proc, port, url


def _holder_process(repo, hold_seconds=30):
    """Separate process that owns the updater lock (retrying until it gets
    it) then exits. Readiness is signalled by creating the sentinel file."""
    ready_file = repo.lock_file + ".holder-ready"
    holder = subprocess.Popen(
        [
            sys.executable, "-c",
            "import os, sys, time;\n"
            "sys.path.insert(0, sys.argv[1]);\n"
            "from update_platform import acquire_lock;\n"
            "while True:\n"
            "    handle = acquire_lock(sys.argv[2])\n"
            "    if handle is not None:\n"
            "        break\n"
            "    time.sleep(0.05)\n"
            "open(sys.argv[4], 'w').write('ready')\n"
            "time.sleep(float(sys.argv[3]));\n",
            os.path.join(REPO_ROOT, "scripts"),
            repo.lock_file,
            str(hold_seconds),
            ready_file,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if os.path.exists(ready_file):
            return holder
        if holder.poll() is not None:
            raise AssertionError(
                "lock holder died: " + holder.stderr.read().decode()
            )
        time.sleep(0.05)
    raise AssertionError("lock holder never became ready")


def test_lock_contention_second_updater_fails_without_mutation(repo):
    holder = _holder_process(repo)
    try:
        # Lock is provably held now; a second updater must report contention.
        probe = repo.run_updater(["--stop"], extra_env={
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        }, timeout=20)

        # Contract: contention reports cleanly with rc 0 and no mutation.
        assert probe.returncode == 0
        assert "Another update operation or update check" in probe.stderr
        assert holder.poll() is None  # holder unaffected
        assert repo.worktree_clean()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)


def test_lock_crash_release_allows_reacquire(repo):
    holder = _holder_process(repo, hold_seconds=60)
    holder.kill()  # abrupt death while owning the kernel lock
    holder.wait(timeout=5)

    # A fresh updater must get past locking: stale-PID --stop succeeds fast.
    with open(repo.pid_file, "w") as handle:
        handle.write("999999999")
    result = repo.run_updater(["--stop"], timeout=30)

    assert result.returncode == 0, result.stderr
    assert not os.path.exists(repo.pid_file)


def test_stale_pid_stop_spares_live_decoy(repo, tracked_processes):
    decoy = subprocess.Popen(
        ["sleep", "60"], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    tracked_processes(decoy)  # defensive cleanup on failure
    with open(repo.pid_file, "w") as handle:
        handle.write(str(decoy.pid + 500000))  # almost surely nonexistent

    result = repo.run_updater(["--stop"], timeout=30)

    assert result.returncode == 0, result.stderr
    assert not os.path.exists(repo.pid_file)
    time.sleep(0.3)
    assert decoy.poll() is None  # unrelated live process untouched
    decoy.terminate()
    decoy.wait(timeout=5)


def test_real_service_start_stop_lifecycle(repo, tracked_processes):
    port = free_port()
    command = stub_start_command(port)
    proc, port, url = _start_managed_service(repo, tracked_processes, command, port)

    repo.remote_set_version("2.0")  # update triggers stop -> restart cycle
    updated = repo.run_updater([], extra_env={
        "WEBAI_START_COMMAND": command,
        "WEBAI_HEALTH_URL": url,
    })
    assert updated.returncode == 0, updated.stderr

    new_pid = read_pid(repo)
    assert wait_process_exit(proc)
    assert health_status(url) == 200
    assert pid_alive(new_pid)

    stopped = repo.run_updater(["--stop"], extra_env={
        "WEBAI_HEALTH_URL": url,
    })
    assert stopped.returncode == 0, stopped.stderr
    assert wait_pid_gone(new_pid)
    assert not os.path.exists(repo.pid_file)
    assert not port_serving(port)


def test_poetry_command_shape_via_exec_shim(repo, tracked_processes):
    """§2: exercise production START_COMMAND shape `poetry run python ...`.

    Transparent `poetry` PATH shim execs its arguments, preserving the
    project venv interpreter without needing a Poetry env in the fixture
    clone. Because the shim uses exec(1), the recorded PID is the actual
    server process — exactly what PID-file ownership requires.
    """
    bin_dir = os.path.join(repo.base, "shape-bin")
    os.makedirs(bin_dir, exist_ok=True)
    shim = os.path.join(bin_dir, "poetry")
    with open(shim, "w") as handle:
        # Real `poetry run CMD...` consumes the `run` subcommand, then
        # executes the rest; the shim mirrors that contract.
        handle.write(
            '#!/usr/bin/env sh\n'
            '[ "$1" = run ] && shift\n'
            'exec "$@"\n'
        )
    os.chmod(shim, 0o755)

    start_command = (
        f"poetry run {sys.executable} "
        f'"{os.path.join(REPO_ROOT, "src", "run.py")}" '
        f"--host 127.0.0.1 --port {{port}}"
    )
    path_env = f"{bin_dir}:{os.environ.get('PATH', '')}"

    # Boot A directly (shim shape) so the updater has something to manage.
    boot_port = free_port()
    probe_command = start_command.format(port=boot_port)
    proc, port_a, url_a = _start_managed_service(
        repo, tracked_processes, probe_command, boot_port
    )
    cmdline_path = f"/proc/{proc.pid}/cmdline"
    if os.path.exists(cmdline_path):  # linux evidence; skip on darwin
        with open(cmdline_path, "rb") as handle:
            cmdline = handle.read().decode(errors="replace")
        assert "run.py" in cmdline  # §19: PID *is* the server process

    # Update with the real default-shape command as the restart command.
    repo.remote_set_version("2.0")
    restart_port = free_port()
    restart_url = f"http://127.0.0.1:{restart_port}/health"
    updated = repo.run_updater([], extra_env={
        "WEBAI_START_COMMAND": start_command.format(port=restart_port),
        "WEBAI_HEALTH_URL": restart_url,
        "PATH": path_env,
    })
    assert updated.returncode == 0, (
        updated.stderr or open(repo.log_file).read()
    )
    new_pid = read_pid(repo)
    assert wait_process_exit(proc)
    assert pid_alive(new_pid)
    assert health_status(restart_url) == 200

    stopped = repo.run_updater(["--stop"], extra_env={
        "WEBAI_HEALTH_URL": restart_url,
        "PATH": path_env,
    })
    assert stopped.returncode == 0, stopped.stderr
    assert wait_pid_gone(new_pid)
    assert not port_serving(restart_port)


def test_real_server_via_run_py_boots_and_stops(repo, tracked_processes):
    """The actual FastAPI app boots headless and stops gracefully."""
    start_command_tpl = (
        f'"{sys.executable}" "{os.path.join(REPO_ROOT, "src", "run.py")}" '
        f"--host 127.0.0.1 --port {{port}}"
    )
    boot_port = free_port()
    boot_url = f"http://127.0.0.1:{boot_port}/health"
    proc, _, _ = _start_managed_service(
        repo, tracked_processes, start_command_tpl.format(port=boot_port),
        boot_port,
    )
    assert wait_health(boot_url, timeout=40)
    assert health_status(boot_url) == 200

    repo.remote_set_version("2.0")
    restart_port = free_port()
    restart_url = f"http://127.0.0.1:{restart_port}/health"
    updated = repo.run_updater([], extra_env={
        "WEBAI_START_COMMAND": start_command_tpl.format(port=restart_port),
        "WEBAI_HEALTH_URL": restart_url,
        "WEBAI_HEALTH_TIMEOUT": "40",
    })
    assert updated.returncode == 0, (
        updated.stderr or open(repo.log_file).read()
    )

    new_pid = read_pid(repo)
    assert pid_alive(new_pid)
    assert health_status(restart_url) == 200

    # Scope evidence to THIS final stop only (byte offsets need binary mode).
    log_offset = os.path.getsize(repo.log_file)

    stopped = repo.run_updater(["--stop"], extra_env={
        "WEBAI_HEALTH_URL": restart_url,
    })
    assert stopped.returncode == 0, stopped.stderr
    assert wait_pid_gone(new_pid)

    with open(repo.log_file, "rb") as handle:
        handle.seek(log_offset)
        tail = handle.read().decode("utf-8", errors="replace")
    assert ("Application shutdown requested" in tail
            or "FastAPI application lifespan shutdown executing." in tail)
    assert health_status(restart_url) is None
    assert not port_serving(restart_port)


def test_update_linux_macos_wrapper_forwards_arguments(repo):
    with open(repo.pid_file, "w") as handle:
        handle.write("999999999")
    wrapper = os.path.join(REPO_ROOT, "update-linux-macos.sh")
    env = repo.env()
    result = subprocess.run(
        ["bash", wrapper, "--stop"],
        cwd=repo.work, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert not os.path.exists(repo.pid_file)


def test_tmp_defaults_are_writable():
    """POSIX /tmp accepts the updater's file pattern without touching the
    real global service filenames."""
    import random

    suffix = f"{os.getpid()}-{random.randint(1000, 9999)}"
    probes = [
        f"/tmp/webai-to-api-integration-{suffix}.pid",
        f"/tmp/webai-to-api-integration-{suffix}.log",
        f"/tmp/webai-to-api-integration-{suffix}.lock",
    ]
    try:
        for path in probes:
            with open(path, "a"):
                pass
            assert os.path.exists(path)
    finally:
        for path in probes:
            try:
                os.unlink(path)
            except OSError:
                pass


def test_real_poetry_process_tree_and_graceful_stop(
    repo, tracked_processes,
):
    """Real `poetry run python src/run.py` through the updater stop policy.

    The outer Poetry PID is written to the updater PID file; the REAL
    updater `--stop` (normal platform graceful-stop policy) must end the
    actual server. Direct-signal behavior is intentionally NOT asserted.
    """
    poetry = shutil.which("poetry")
    if poetry is None:
        pytest.skip("Poetry not available for real process-tree integration")

    port = free_port()
    url = f"http://127.0.0.1:{port}/health"
    log = open(repo.log_file, "ab")
    # Production command shape: literal `python` resolves through Poetry's
    # project virtualenv exactly like a real installation.
    proc = subprocess.Popen(
        ["poetry", "run", "python",
         os.path.join(REPO_ROOT, "src", "run.py"),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    tracked_processes(proc)
    with open(repo.pid_file, "w") as handle:
        handle.write(str(proc.pid))  # outer PID is what the updater manages

    try:
        assert wait_health(url, timeout=60), open(repo.log_file).read()
        assert pid_alive(proc.pid)

        if sys.platform == "linux":
            children_before = subprocess.run(
                ["ps", "--ppid", str(proc.pid), "-o", "pid=,cmd="],
                capture_output=True, text=True,
            ).stdout.strip()
            print(f"poetry tree before stop:\n{children_before}")

        stopped = repo.run_updater(["--stop"], extra_env={
            "WEBAI_HEALTH_URL": url,
        })
        assert stopped.returncode == 0, stopped.stderr

        assert wait_process_exit(proc, timeout=30)
        assert not os.path.exists(repo.pid_file)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not port_serving(port):
                break
            time.sleep(0.2)
        else:
            tree_note = children_before if sys.platform == "linux" else "n/a"
            raise AssertionError(
                "orphan WebAI server still serving after updater --stop "
                f"(tree was:\n{tree_note})"
            )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
