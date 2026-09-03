# src/app/shutdown.py
"""
Generic application shutdown state.

Process-global, thread-safe, idempotent intent flag.
First shutdown source wins. Readable from ASGI middleware,
callable from POSIX signal handler and Windows IPC thread.
No async teardown here.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_requested: bool = False
_source: str | None = None


def request_shutdown(source: str = "application") -> bool:
    """
    Mark application shutdown intent.

    Returns True only for the first accepted request.
    Subsequent calls return False and preserve the original source.
    """
    global _requested, _source
    with _lock:
        if _requested:
            return False
        _requested = True
        _source = source
        return True


def is_shutdown_requested() -> bool:
    """Thread-safe read of shutdown intent."""
    with _lock:
        return _requested


def shutdown_source() -> str | None:
    """First shutdown source, or None if not yet requested."""
    with _lock:
        return _source


def _reset_for_tests() -> None:
    """
    Test-only reset of global state.

    Not for production use. Exposed for deterministic unit tests
    that need to isolate shutdown transitions.
    """
    global _requested, _source
    with _lock:
        _requested = False
        _source = None
