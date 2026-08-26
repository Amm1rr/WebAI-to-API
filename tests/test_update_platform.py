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

POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="POSIX-only platform mechanics"
)


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
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2)"]
    )
    try:
        assert platform.pid_alive(proc.pid) is True
    finally:
        proc.wait()


def test_dead_pid_is_not_alive():
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert platform.pid_alive(finished.pid) is False


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Linux /proc zombie detection; macOS kill(0) fallback may "
           "report zombies alive until reaped (accepted latency nuance)",
)
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


@POSIX_ONLY
def test_terminate_graceful_uses_sigterm(monkeypatch):
    sent = []
    monkeypatch.setattr(platform.os, "kill",
                        lambda pid, sig: sent.append((pid, sig)))
    platform.terminate_graceful(1234)
    assert sent == [(1234, signal.SIGTERM)]


@POSIX_ONLY
def test_force_kill_uses_sigkill(monkeypatch):
    sent = []
    monkeypatch.setattr(platform.os, "kill",
                        lambda pid, sig: sent.append((pid, sig)))
    platform.force_kill(1234)
    assert sent == [(1234, signal.SIGKILL)]


@POSIX_ONLY
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


@POSIX_ONLY
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


@POSIX_ONLY
def test_spawn_failure_preserves_legacy_strerror_wording(tmp_path):
    """user_message must match pre-extraction `error.strerror or error`."""
    log = open(tmp_path / "out.log", "ab")
    with pytest.raises(platform.PlatformOperationError) as excinfo:
        platform.spawn_detached(
            ["/nonexistent/binary-xyz"], cwd=str(tmp_path), log_handle=log
        )
    assert excinfo.value.user_message == "No such file or directory"
    log.close()


@pytest.mark.skipif(os.name != "posix", reason="fcntl is POSIX-only")
def test_flock_close_failure_does_not_mask_primary_error(
    tmp_path, monkeypatch
):
    import errno

    lock_path = str(tmp_path / "update.lock")

    real_flock = platform.fcntl.flock
    real_close = os.close
    captured_fds = []
    close_failed = False

    def flock_eio(fd, operation):
        raise OSError(errno.EIO, "Simulated I/O error")

    def close_boom(fd):
        # Explode on the FIRST production cleanup close so the primary
        # error path is exercised; delegate to the real close afterwards
        # so the descriptor cannot leak past this test.
        nonlocal close_failed
        captured_fds.append(fd)
        if not close_failed:
            close_failed = True
            raise OSError(errno.EBADF, "Simulated close failure")
        return real_close(fd)

    captured_fd = None
    monkeypatch.setattr(platform.fcntl, "flock", flock_eio)
    monkeypatch.setattr(platform.os, "close", close_boom)
    try:
        with pytest.raises(platform.PlatformOperationError) as excinfo:
            platform.acquire_lock(lock_path)
    finally:
        monkeypatch.setattr(platform.os, "close", real_close)
        # Restore a usable flock for later fixture teardown paths.
        monkeypatch.setattr(platform.fcntl, "flock", real_flock)
        if captured_fds:
            captured_fd = captured_fds[0]
            try:
                real_close(captured_fd)  # production already failed once
            except OSError:
                pass

    assert excinfo.value.phase == "flock"
    assert excinfo.value.original_error.errno == errno.EIO
    assert "Simulated I/O error" in str(excinfo.value)

    # FD hygiene: the real descriptor is closed before the test exits,
    # without relying on process teardown.
    assert captured_fd is not None
    with pytest.raises(OSError):
        os.fstat(captured_fd)


# --- Updater Python contract guard ------------------------------------------


def _guard_source_and_prefix():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "update.py",
    )
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    return path, source


@pytest.mark.parametrize(
    ("version", "expected_ok"),
    [
        ((3, 10), False),
        ((3, 11), True),
        ((3, 12), True),
        ((3, 13), False),
    ],
)
def test_updater_python_contract_predicate(version, expected_ok):
    import importlib.util

    path, _ = _guard_source_and_prefix()
    spec = importlib.util.spec_from_file_location("update_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # supported interpreter: loads fully
    assert module._python_version_supported(version + (0, 0, "final")) is expected_ok


def test_unsupported_python_gets_clean_exit_before_heavy_imports(monkeypatch):
    """Execute only the guarded prefix as Python 3.10 would reach it."""
    import types

    path, source = _guard_source_and_prefix()
    prefix = source.split("import json", 1)[0]
    # Ordering pinned: the SystemExit guard must precede any 3.11-dependent
    # import (the docstring may mention tomllib; only real imports matter).
    assert "import tomllib" not in prefix

    fake_stderr = types.SimpleNamespace()
    fake_stderr.buffer_write = []
    written = []

    class StdErr:
        def write(self, text):
            written.append(text)

    monkeypatch.setattr(sys, "stderr", StdErr())
    monkeypatch.setattr(sys, "version_info", (3, 10, 0, "final", "final"))

    namespace = {"__name__": "update_guarded_prefix"}
    code = compile(prefix, path, "exec")
    try:
        exec(code, namespace)
    except SystemExit as exit_error:
        assert exit_error.code == 1
    else:
        raise AssertionError("guard did not exit on unsupported Python")

    output = "".join(written)
    assert ">=3.11,<3.13" in output
    assert "3.10" in output


# --- Windows platform adapter (semantics mocked; real Windows = Phase 6) -----

import ctypes
import errno
import types

import update_platform


class FakeMsvcrt:
    """Records locking() calls; scripted failures per (fd, mode)."""

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or {}

    def locking(self, fd, mode, nbytes):
        self.calls.append((fd, mode, nbytes))
        failure = self.fail_on.get(mode)
        if failure is not None:
            raise failure
        return None


@pytest.fixture
def windows_platform(monkeypatch):
    """Force the Windows branch with an injectable msvcrt fake."""
    fake = FakeMsvcrt()
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(platform, "msvcrt", fake, raising=False)
    return fake


def _errno_oserror(errno_value):
    return OSError(errno_value, os.strerror(errno_value))


def test_windows_imports_are_platform_gated():
    """fcntl must sit under the POSIX branch; msvcrt/ctypes under nt."""
    import ast as ast_module

    source_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "scripts", "update_platform.py",
    )
    with open(source_path, encoding="utf-8") as handle:
        tree = ast_module.parse(handle.read())

    unconditional, posix_side, nt_side = set(), set(), set()
    for node in tree.body:
        if isinstance(node, ast_module.Import):
            for alias in node.names:
                unconditional.add(alias.name)
        elif isinstance(node, ast_module.If):
            test_source = ast_module.unparse(node.test)
            is_nt_check = '"nt"' in test_source or "'nt'" in test_source
            for inner in ast_module.walk(ast_module.Module(
                body=node.body, type_ignores=[]
            )):
                if isinstance(inner, ast_module.Import):
                    for alias in inner.names:
                        (nt_side if is_nt_check else posix_side).add(alias.name)
            for inner in ast_module.walk(ast_module.Module(
                body=node.orelse, type_ignores=[]
            )):
                if isinstance(inner, ast_module.Import):
                    for alias in inner.names:
                        (posix_side if is_nt_check else nt_side).add(alias.name)

    assert "fcntl" not in unconditional
    assert "fcntl" in posix_side
    assert "msvcrt" in nt_side and "ctypes" in nt_side


def test_windows_lock_initializes_byte_and_locks(windows_platform, tmp_path):
    lock_path = tmp_path / "win.lock"
    handle = platform.acquire_lock(str(lock_path))

    assert handle is not None
    assert lock_path.stat().st_size >= 1  # lockable byte exists
    assert windows_platform.calls[0][1] == FakeMsvcrt.LK_NBLCK
    assert windows_platform.calls[0][2] == 1
    handle.release()
    assert any(mode == FakeMsvcrt.LK_UNLCK for _, mode, _ in windows_platform.calls)


def test_windows_lock_contention_returns_none(monkeypatch, tmp_path):
    monkeypatch.delattr(errno, "EDEADLOCK", raising=False)
    fallback_errno = getattr(errno, "EDEADLK", errno.EACCES)
    for contention_errno in (errno.EACCES, fallback_errno):
        fake = FakeMsvcrt(fail_on={FakeMsvcrt.LK_NBLCK: _errno_oserror(contention_errno)})
        monkeypatch.setattr(platform, "IS_WINDOWS", True)
        monkeypatch.setattr(platform, "msvcrt", fake, raising=False)
        lock_path = tmp_path / f"lock-{contention_errno}"
        assert platform.acquire_lock(str(lock_path)) is None  # expected path


def test_windows_lock_unexpected_error_normalized(monkeypatch, tmp_path):
    fake = FakeMsvcrt(fail_on={FakeMsvcrt.LK_NBLCK: _errno_oserror(errno.EBADF)})
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(platform, "msvcrt", fake, raising=False)
    with pytest.raises(platform.PlatformOperationError) as excinfo:
        platform.acquire_lock(str(tmp_path / "bad.lock"))
    assert excinfo.value.phase == "flock"
    assert isinstance(excinfo.value.original_error, OSError)


def test_windows_release_is_idempotent(windows_platform, tmp_path):
    handle = platform.acquire_lock(str(tmp_path / "idem.lock"))
    calls_before = len(windows_platform.calls)
    handle.release()
    handle.release()  # second call must not re-unlock or raise
    unlock_calls = [c for c in windows_platform.calls[calls_before:] if c[1] == FakeMsvcrt.LK_UNLCK]
    assert len(unlock_calls) == 1


@POSIX_ONLY
def test_posix_lock_branch_unchanged(monkeypatch, tmp_path):
    """POSIX flag still routes through flock semantics."""
    lock_path = tmp_path / "posix.lock"
    monkeypatch.setattr(platform, "IS_WINDOWS", False)
    handle = platform.acquire_lock(str(lock_path))
    assert handle is not None and handle._windows is False
    other = platform.acquire_lock(str(lock_path))
    assert other is None  # real flock contention on this platform
    handle.release()


class FakeKernel32:
    """Mirrors the real Win32 boundary: OpenProcess failures publish a
    last-error code into the ctypes snapshot slot instead of exposing any
    fake `get_last_error` method."""

    def __init__(self, open_result=1, open_error=0, exit_code=259,
                 query_succeeds=True):
        self.open_result = open_result
        self.open_error = open_error
        self.exit_code = exit_code
        self.query_succeeds = query_succeeds
        self.opened = []
        self.closed = []
        self._next_handle = 4321
        self.state = {"last": 0}

    def OpenProcess(self, access, inherit, pid):
        if self.open_result == 0:
            self.state["last"] = self.open_error  # kernel records error
            return 0
        self.state["last"] = 0
        self.opened.append(pid)
        self.last_handle = self._next_handle
        self._next_handle += 1
        return self.last_handle

    def GetExitCodeProcess(self, handle, byref_target):
        if not self.query_succeeds:
            self.state["last"] = 6  # ERROR_INVALID_HANDLE
            return 0
        byref_target._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def _with_fake_kernel32(monkeypatch, fake):
    """Windows branch + ctypes namespace whose get_last_error() snapshots
    what the fake kernel recorded (mirrors use_last_error=True semantics)."""
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    state = fake.state

    class SnapshotNamespace(types.SimpleNamespace):
        pass

    snapshot = SnapshotNamespace(
        c_uint32=ctypes.c_uint32,
        byref=ctypes.byref,
        get_last_error=lambda: state["last"],
        set_last_error=lambda value: state.__setitem__("last", value),
    )
    monkeypatch.setattr(platform, "ctypes", snapshot, raising=False)
    monkeypatch.setattr(platform, "_KERNEL32", fake)
    return fake


def test_windows_liveness_live_process(monkeypatch):
    fake = _with_fake_kernel32(
        monkeypatch, FakeKernel32(exit_code=259)  # STILL_ACTIVE
    )
    assert platform.pid_alive(1234) is True
    assert fake.closed == [fake.last_handle]  # handle deterministically closed


def test_windows_liveness_exited_process(monkeypatch):
    fake = _with_fake_kernel32(monkeypatch, FakeKernel32(exit_code=0))
    assert platform.pid_alive(1234) is False
    assert len(fake.closed) == 1


def test_windows_liveness_nonexistent_pid(monkeypatch):
    fake = _with_fake_kernel32(
        monkeypatch, FakeKernel32(open_result=0, open_error=87)
    )
    assert platform.pid_alive(999999) is False
    assert fake.closed == []


def test_windows_liveness_access_denied_means_alive(monkeypatch):
    _with_fake_kernel32(
        monkeypatch, FakeKernel32(open_result=0, open_error=5)
    )
    assert platform.pid_alive(5) is True


def test_windows_liveness_unknown_openerror_raises(monkeypatch):
    fake = _with_fake_kernel32(
        monkeypatch, FakeKernel32(open_result=0, open_error=1455)
    )
    with pytest.raises(platform.PlatformOperationError) as excinfo:
        platform.pid_alive(888)
    assert excinfo.value.phase == "liveness"
    error = excinfo.value.original_error
    if os.name == "nt":
        assert error.winerror == 1455
    else:
        assert 1455 in error.args
    assert fake.closed == []  # nothing to close: never opened


def test_windows_liveness_query_failure_raises_normalized(monkeypatch):
    fake = _with_fake_kernel32(monkeypatch, FakeKernel32(query_succeeds=False))
    with pytest.raises(platform.PlatformOperationError) as excinfo:
        platform.pid_alive(777)
    assert excinfo.value.phase == "liveness"
    error = excinfo.value.original_error
    if os.name == "nt":
        assert error.winerror == 6
    else:
        assert 6 in error.args
    assert len(fake.closed) == 1  # valid HANDLE closed on failure path too


def test_windows_force_kill_uses_hard_termination(monkeypatch):
    kills = []

    def fake_kill(pid, sig):
        kills.append((pid, sig))

    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "kill", fake_kill)
    platform.force_kill(4242)
    assert kills == [(4242, signal.SIGTERM)]  # TerminateProcess mapping


def test_windows_force_kill_already_gone_tolerated(monkeypatch):
    def fake_kill(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "kill", fake_kill)
    platform.force_kill(4242)  # no exception


def test_windows_force_kill_operational_failure_normalized(monkeypatch):
    def fake_kill(pid, sig):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "kill", fake_kill)
    with pytest.raises(platform.PlatformOperationError) as excinfo:
        platform.force_kill(4242)
    assert excinfo.value.phase == "force"


def test_windows_graceful_signal_forbidden(monkeypatch):
    """terminate_graceful must fail loudly on Windows, never hard-kill."""
    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    with pytest.raises(RuntimeError, match="IPC"):
        platform.terminate_graceful(123)


def test_windows_spawn_flags(monkeypatch, tmp_path):
    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured.update(kwargs)
            self.pid = 5555

        def kill(self):
            pass

    monkeypatch.setattr(platform, "IS_WINDOWS", True)
    expected_flags = 0x20000200  # arbitrary recognizable sentinel
    monkeypatch.setattr(
        platform, "_windows_creation_flags", lambda: expected_flags
    )
    monkeypatch.setattr(platform.subprocess, "Popen", FakePopen)
    log = open(tmp_path / "win.log", "ab")
    process = platform.spawn_detached(
        ["C:\\fake\\service.exe"], cwd=str(tmp_path), log_handle=log
    )
    log.close()
    assert process.pid == 5555
    assert "start_new_session" not in captured
    assert captured["creationflags"] == expected_flags
