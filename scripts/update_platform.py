"""
Platform mechanics for the WebAI-to-API updater (POSIX/Linux + Windows).

Owns only OS-specific primitives: crash-safe locking, process liveness,
graceful/force stop delivery and detached spawning. Updater policy (grace
timing, retry, IPC wiring, messages, PID-file rules) stays in
scripts/update.py.

Errors are normalized to PlatformOperationError carrying the original OS
error text; expected lock contention is returned as None, never raised.
Windows graceful shutdown intentionally does NOT live here: os.kill on
Windows maps non-console signals to abrupt TerminateProcess, so the
updater routes graceful stops through the Phase 4 IPC transport instead
(see request_service_shutdown in scripts/update.py).
"""

import errno
import os
import signal
import subprocess

if os.name == "nt":
    import ctypes
    import msvcrt
else:
    import fcntl

IS_WINDOWS = os.name == "nt"

# Windows constants (used only on nt; harmless integers elsewhere).
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87
_ERROR_ACCESS_DENIED = 5


def _windows_lock_contention_errno():
    """Return alternate deadlock errno used by Windows byte-range locks."""
    return getattr(errno, "EDEADLOCK", getattr(errno, "EDEADLK", errno.EACCES))

_KERNEL32 = None


def _kernel32():
    """Load and configure kernel32 once (Windows only)."""
    global _KERNEL32
    if _KERNEL32 is None:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        library.OpenProcess.restype = ctypes.c_void_p
        library.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        library.GetExitCodeProcess.restype = ctypes.c_int
        library.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        library.CloseHandle.restype = ctypes.c_int
        library.CloseHandle.argtypes = [ctypes.c_void_p]
        _KERNEL32 = library
    return _KERNEL32


class PlatformOperationError(RuntimeError):
    """
    An expected OS/subprocess operation failed.

    Carries the original OSError for legacy-compatible wording
    (`user_message` prefers strerror, matching pre-extraction messages)
    and an optional `phase` tag (e.g. "open"/"flock" for lock failures).
    """

    def __init__(self, error, *, phase=None):
        self.original_error = error
        self.phase = phase
        super().__init__(str(error))

    @property
    def user_message(self):
        return getattr(self.original_error, "strerror", None) or str(
            self.original_error
        )


class LockHandle:
    """Owns the locked fd; release() unlocks+closes best-effort, idempotent."""

    def __init__(self, fd, windows=False):
        self._fd = fd
        self._windows = windows
        self._released = False

    def release(self):
        if self._released:
            return
        self._released = True
        try:
            if self._windows:
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass


def acquire_lock(path):
    """
    Acquire an exclusive non-blocking kernel lock on `path`.

    Returns LockHandle on success, None when another process holds the lock
    (contention). Any other failure raises PlatformOperationError. The
    kernel releases the lock when the process dies, so no stale cleanup
    protocol exists or is needed.

    POSIX: flock(LOCK_EX|LOCK_NB); contention errno EACCES/EAGAIN.
    Windows: msvcrt.locking(LK_NBLCK) on one initialized byte at offset 0;
    contention surfaces as EACCES ("Being used by another process") — the
    documented mapping; EDEADLOCK is included as the observed alternate
    errno for contended byte-range locks on Windows.
    """
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as error:
        raise PlatformOperationError(error, phase="open") from error
    if IS_WINDOWS:
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")  # msvcrt needs a lockable byte range
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            try:
                os.close(fd)  # best-effort; never masks the primary error
            except OSError:
                pass
            if error.errno in (errno.EACCES, _windows_lock_contention_errno()):
                return None  # expected contention: another updater owns it
            raise PlatformOperationError(error, phase="flock") from error
        return LockHandle(fd, windows=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        try:
            os.close(fd)  # best-effort cleanup; never masks the primary error
        except OSError:
            pass
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return None  # expected contention: another updater owns it
        raise PlatformOperationError(error, phase="flock") from error
    return LockHandle(fd)


def _winerror_oserror(code):
    """Build an OSError carrying the actual Win32 last-error code."""
    return OSError(code, f"Win32 error {code}", None, code)


def _windows_pid_alive(pid):
    """Win32 liveness via OpenProcess+GetExitCodeProcess.

    dead/nonexistent (ERROR_INVALID_PARAMETER) -> False;
    ERROR_ACCESS_DENIED -> True (exists, protected);
    other Win32 failures -> PlatformOperationError(phase="liveness")
    carrying an OSError with the real last-error code. Valid HANDLEs are
    always closed. Uses ctypes.get_last_error(), which is the correct
    thread-local snapshot for WinDLL(..., use_last_error=True).
    """
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        error_code = ctypes.get_last_error()
        if error_code == _ERROR_INVALID_PARAMETER:
            return False  # no such process
        if error_code == _ERROR_ACCESS_DENIED:
            return True  # exists but cannot be opened
        raise PlatformOperationError(
            _winerror_oserror(error_code), phase="liveness"
        ) from None
    try:
        exit_code = ctypes.c_uint32(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise PlatformOperationError(
                _winerror_oserror(ctypes.get_last_error()),
                phase="liveness",
            ) from None
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid):
    """
    Liveness probe.

    Windows: OpenProcess/GetExitCodeProcess (STILL_ACTIVE check); no
    zombies exist there. Linux: /proc/<pid>/stat zombie awareness with
    kill(pid, 0) fallback. Other POSIX (macOS): kill(pid, 0) fallback — a
    not-yet-reaped zombie may read as alive until reaped, an accepted
    latency-only nuance handled by the grace budget.
    """
    if IS_WINDOWS:
        return _windows_pid_alive(pid)
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            state = handle.read().rsplit(")", 1)[1].split()[0]
            return state != "Z"
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False
    return True


def terminate_graceful(pid):
    """Deliver SIGTERM (POSIX only).

    On Windows this function must never be used: Python maps non-console
    signals to abrupt TerminateProcess, which would silently hard-kill the
    service. The updater routes Windows graceful stops through the Phase 4
    IPC transport instead, so accidental use fails loudly here.
    """
    if IS_WINDOWS:
        raise RuntimeError(
            "terminate_graceful() must not be used on Windows; use the "
            "IPC-based request_service_shutdown() in update.py instead."
        )
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise PlatformOperationError(error, phase="terminate") from error


def force_kill(pid):
    """Hard-terminate the service process.

    POSIX: SIGKILL. Windows: os.kill with SIGTERM maps to abrupt
    TerminateProcess semantics (the only stdlib single-PID hard kill), so
    the specific signal value is irrelevant there by design.
    A vanished process counts as already gone.
    """
    sig = signal.SIGTERM if IS_WINDOWS else signal.SIGKILL
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise PlatformOperationError(error, phase="force") from error


def _windows_creation_flags():
    """Windows-only detached-process creation flags."""
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def spawn_detached(argv, cwd, log_handle):
    """
    Start a detached background process writing stdout/stderr to
    log_handle. Returns the live subprocess.Popen object so callers retain
    kill() for post-spawn failure handling (e.g., PID-file write errors).

    POSIX: new session via start_new_session. Windows: new process group +
    CREATE_NO_WINDOW so no console is attached or flashed.
    """
    platform_kwargs = {}
    if IS_WINDOWS:
        platform_kwargs["creationflags"] = _windows_creation_flags()
    else:
        platform_kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **platform_kwargs,
        )
    except OSError as error:
        raise PlatformOperationError(error, phase="spawn") from error
