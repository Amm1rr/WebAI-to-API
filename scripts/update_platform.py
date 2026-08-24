"""
Platform mechanics for the WebAI-to-API updater (POSIX/Linux).

Owns only OS-specific primitives: crash-safe locking, process liveness,
signal delivery and detached spawning. Updater policy (grace timing, retry,
messages, PID-file rules) stays in scripts/update.py.

Errors are normalized to PlatformOperationError carrying the original OS
error text; expected lock contention is returned as None, never raised.
"""

import errno
import fcntl
import os
import signal
import subprocess


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

    def __init__(self, fd):
        self._fd = fd
        self._released = False

    def release(self):
        if self._released:
            return
        self._released = True
        try:
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
    (EACCES/EAGAIN). Any other failure raises PlatformOperationError. The
    kernel releases the lock when the process dies, so no stale cleanup
    protocol exists or is needed.
    """
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as error:
        raise PlatformOperationError(error, phase="open") from error
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


def pid_alive(pid):
    """
    Linux liveness with zombie awareness: /proc/<pid>/stat state 'Z' counts
    as dead; otherwise falls back to os.kill(pid, 0) signal-probing.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            state = handle.read().rsplit(")", 1)[1].split()[0]
            return state != "Z"
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_graceful(pid):
    """Deliver SIGTERM. A vanished process counts as already stopped."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise PlatformOperationError(error, phase="terminate") from error


def force_kill(pid):
    """Deliver SIGKILL. A vanished process counts as already gone."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise PlatformOperationError(error, phase="force") from error


def spawn_detached(argv, cwd, log_handle):
    """
    Start a background session-leader process writing stdout/stderr to
    log_handle. Returns the live subprocess.Popen object so callers retain
    kill() for post-spawn failure handling (e.g., PID-file write errors).
    """
    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as error:
        raise PlatformOperationError(error, phase="spawn") from error
