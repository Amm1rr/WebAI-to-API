"""
Focused tests for scripts/update_platform.py (POSIX/Linux mechanics).

Policy lives in the updater; these pin only the extracted mechanics.
"""

import os
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
))

import update_platform as platform


@pytest.fixture
def lock_path(tmp_path):
    return str(tmp_path / "update.lock")


def test_first_lock_acquire_succeeds(lock_path):
    handle = platform.acquire_lock(lock_path)
    assert handle is not None
    handle.release()


def test_second_acquire_on_same_file_returns_none(lock_path):
    first = platform.acquire_lock(lock_path)
    try:
        assert platform.acquire_lock(lock_path) is None
    finally:
        first.release()


def test_release_then_reacquire_succeeds(lock_path):
    first = platform.acquire_lock(lock_path)
    first.release()
    second = platform.acquire_lock(lock_path)
    assert second is not None
    second.release()


def test_release_is_idempotent(lock_path):
    handle = platform.acquire_lock(lock_path)
    handle.release()
    handle.release()  # must not raise


def test_lock_open_error_raises_platform_error(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.raises(platform.PlatformOperationError):
        platform.acquire_lock(str(blocker / "child.lock"))


def test_live_process_is_alive():
    proc = subprocess.Popen(["sleep", "0.2"])
    try:
        assert platform.pid_alive(proc.pid) is True
    finally:
        proc.wait()


def test_dead_pid_is_not_alive():
    finished = subprocess.Popen(["true"])
    finished.wait()
    assert platform.pid_alive(finished.pid) is False


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX zombie semantics")
def test_zombie_child_counts_as_dead():
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if platform.pid_alive(pid) is False:
            break
        time.sleep(0.01)
    else:
        os.waitpid(pid, 0)
        pytest.fail("zombie child was reported alive")
    os.waitpid(pid, 0)


def test_terminate_graceful_uses_sigterm(monkeypatch):
    sent = []
    monkeypatch.setattr(platform.os, "kill",
                        lambda pid, sig: sent.append((pid, sig)))
    platform.terminate_graceful(1234)
    assert sent == [(1234, signal.SIGTERM)]


def test_force_kill_uses_sigkill(monkeypatch):
    sent = []
    monkeypatch.setattr(platform.os, "kill",
                        lambda pid, sig: sent.append((pid, sig)))
    platform.force_kill(1234)
    assert sent == [(1234, signal.SIGKILL)]


def test_already_gone_process_tolerated(monkeypatch):
    def raise_lookup(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(platform.os, "kill", raise_lookup)
    platform.terminate_graceful(404)
    platform.force_kill(404)  # neither raises


def test_detached_spawn_returns_popen(tmp_path):
    log = open(tmp_path / "out.log", "ab")
    process = platform.spawn_detached(
        [sys.executable, "-c", "pass"], cwd=str(tmp_path), log_handle=log
    )
    try:
        assert isinstance(process, subprocess.Popen)
    finally:
        process.wait()
        log.close()


def test_detached_spawn_uses_start_new_session(tmp_path, monkeypatch):
    captured = {}
    real_popen = subprocess.Popen

    class SpyPopen(real_popen):
        def __init__(self, argv, **kwargs):
            captured.update(kwargs)
            super().__init__(argv, **kwargs)

    monkeypatch.setattr(platform.subprocess, "Popen", SpyPopen)
    log = open(tmp_path / "out.log", "ab")
    process = platform.spawn_detached(
        [sys.executable, "-c", "pass"], cwd=str(tmp_path), log_handle=log
    )
    process.wait()
    log.close()
    assert captured.get("start_new_session") is True
    assert captured.get("stderr") == subprocess.STDOUT


def test_spawn_failure_raises_platform_error(tmp_path):
    log = open(tmp_path / "out.log", "ab")
    with pytest.raises(platform.PlatformOperationError):
        platform.spawn_detached(
            ["/nonexistent/binary-xyz"], cwd=str(tmp_path), log_handle=log
        )
    log.close()


def test_spawn_failure_preserves_legacy_strerror_wording(tmp_path):
    """user_message must match pre-extraction `error.strerror or error`."""
    log = open(tmp_path / "out.log", "ab")
    with pytest.raises(platform.PlatformOperationError) as excinfo:
        platform.spawn_detached(
            ["/nonexistent/binary-xyz"], cwd=str(tmp_path), log_handle=log
        )
    assert excinfo.value.user_message == "No such file or directory"
    log.close()


def test_flock_close_failure_does_not_mask_primary_error(
    tmp_path, monkeypatch
):
    import errno

    lock_path = str(tmp_path / "update.lock")

    real_flock = platform.fcntl.flock
    real_close = os.close

    def flock_eio(fd, operation):
        raise OSError(errno.EIO, "Simulated I/O error")

    def close_boom(fd):
        # Only explode on the lock fd cleanup, not on unrelated closes.
        raise OSError(errno.EBADF, "Simulated close failure")

    monkeypatch.setattr(platform.fcntl, "flock", flock_eio)
    monkeypatch.setattr(platform.os, "close", close_boom)
    try:
        with pytest.raises(platform.PlatformOperationError) as excinfo:
            platform.acquire_lock(lock_path)
    finally:
        monkeypatch.setattr(platform.os, "close", real_close)
        # Restore a usable flock for later fixture teardown paths.
        monkeypatch.setattr(platform.fcntl, "flock", real_flock)

    assert excinfo.value.phase == "flock"
    assert excinfo.value.original_error.errno == errno.EIO
    assert "Simulated I/O error" in str(excinfo.value)
