"""Pytest fixtures built on the shared integration harness helpers."""

import subprocess
import sys

import pytest

from . import _harness
from ._harness import IntegrationRepo  # noqa: F401  (re-export convenience)


@pytest.fixture
def repo(tmp_path):
    return IntegrationRepo(tmp_path)


@pytest.fixture
def tracked_processes():
    procs = []

    def register(proc):
        procs.append(proc)
        return proc

    yield register
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
def service_env_overrides(repo):
    """Structured argv + command string for a stub service on a free port.

    start_argv    -> harness spawns this directly (no shell)
    start_command -> the WEBAI_START_COMMAND value the updater itself uses
    """
    port = _harness.free_port()
    url = f"http://127.0.0.1:{port}/health"
    return {
        "port": port,
        "url": url,
        "start_argv": [sys.executable, _harness.SERVICE_STUB, str(port)],
        "start_command": _harness.stub_start_command(port),
        "env": {
            "WEBAI_START_COMMAND": _harness.stub_start_command(port),
            "WEBAI_HEALTH_URL": url,
        },
    }
