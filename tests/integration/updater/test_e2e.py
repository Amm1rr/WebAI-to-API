"""End-to-end updater scenarios: no-op, update success, rollback.

Real local Git repos, real updater subprocess, real service processes.
The managed service is started as a real detached process using the same
START_COMMAND the updater will later use for restarts. Rollback is triggered
through fixture behavior only (a tracked `.fail-health` file published with
version B makes the new release fail its health gate; resetting to A removes
it). No production code is modified by these tests.
"""

import os
import sys
import time

import pytest

from ._harness import (  # noqa: E402
    cleanup_managed_service,
    health_status,
    pid_alive,
    read_pid,
    spawn_service,
    wait_health,
    wait_process_exit,
)

pytestmark = [pytest.mark.timeout(180)]


def _start_managed(repo, tracked_processes, svc):
    """Start the service with real argv (no shell), like the updater does."""
    proc = spawn_service(
        repo,
        list(svc["start_argv"]),
        tracked_processes,
        ready_url=svc["url"],
        env=repo.env(),
    )
    with open(repo.pid_file, "w") as handle:
        handle.write(str(proc.pid))
    return proc


def test_no_op_when_local_equals_remote(repo, tracked_processes, service_env_overrides):
    svc = service_env_overrides
    proc = _start_managed(repo, tracked_processes, svc)

    try:
        # Versions are equal in a fresh fixture -> pure no-op.
        noop = repo.run_updater([], extra_env={
            "WEBAI_HEALTH_URL": svc["url"],
            "WEBAI_HEALTH_TIMEOUT": "3",
            "WEBAI_HEALTH_INTERVAL": "0.2",
        })

        assert noop.returncode == 0, noop.stderr
        assert proc.poll() is None                      # service untouched
        assert int(open(repo.pid_file).read()) == proc.pid  # not restarted
        assert "Stopping WebAI-to-API" not in noop.stdout + noop.stderr
        assert "poetry install" not in (noop.stdout + noop.stderr).lower()
        assert health_status(svc["url"]) == 200
    finally:
        cleanup_managed_service(
            repo, svc["url"], svc["port"], process=proc
        )


def test_update_success_moves_head_and_restarts_service(
    repo, tracked_processes, service_env_overrides
):
    svc = service_env_overrides
    old_proc = _start_managed(repo, tracked_processes, svc)
    old_pid = old_proc.pid

    try:
        repo.remote_set_version("2.0")  # version-only: dependency sync skipped

        updated = repo.run_updater([], extra_env={
            "WEBAI_START_COMMAND": svc["start_command"],
            "WEBAI_HEALTH_URL": svc["url"],
        })
        assert updated.returncode == 0, updated.stderr

        new_pid = read_pid(repo)
        assert new_pid != old_pid
        assert pid_alive(new_pid)
        assert 'version = "2.0"' in repo.read("pyproject.toml")
        assert repo.worktree_clean()
        assert health_status(svc["url"]) == 200
        assert wait_process_exit(old_proc, timeout=10)
    finally:
        cleanup_managed_service(repo, svc["url"], svc["port"])


def test_rollback_restores_previous_release_when_new_version_fails_health(
    repo, tracked_processes, service_env_overrides
):
    svc = service_env_overrides
    old_proc = _start_managed(repo, tracked_processes, svc)
    sha_a = repo.head()
    assert health_status(svc["url"]) == 200

    try:
        # Version B ships a tracked file that forces its /health to 500.
        # After rollback resets to A the marker disappears and A serves 200.
        fail_marker_rel = ".fail-health"
        fail_marker_abs = os.path.join(repo.work, fail_marker_rel)
        repo.remote_set_version("2.0", extra_files={fail_marker_rel: "500\n"})
        assert repo.head() == sha_a  # work clone still on A before update

        rolled_back = repo.run_updater([], extra_env={
            "WEBAI_START_COMMAND": svc["start_command"],
            "WEBAI_HEALTH_URL": svc["url"],
            "WEBAI_HEALTH_TIMEOUT": "3",
            "WEBAI_HEALTH_INTERVAL": "0.2",
        })

        assert rolled_back.returncode != 0
        combined = rolled_back.stdout + rolled_back.stderr
        assert "ROLLBACK FAILED" not in combined      # rollback itself succeeded
        assert repo.head() == sha_a                   # HEAD restored
        assert 'version = "1.0"' in repo.read("pyproject.toml")
        assert not os.path.exists(fail_marker_abs)    # marker gone with reset
        assert repo.worktree_clean()

        # Previous release restarted and healthy again.
        deadline = time.monotonic() + 30
        pid_b = None
        while time.monotonic() < deadline:
            try:
                pid_b = read_pid(repo)
                break
            except (OSError, ValueError):
                time.sleep(0.2)
        assert pid_b is not None, "PID file missing after rollback restart"
        assert wait_health(svc["url"], timeout=30)
        assert pid_alive(pid_b)
    finally:
        cleanup_managed_service(repo, svc["url"], svc["port"])


def test_explicit_spaces_checkout_end_to_end(tmp_path, tracked_processes,
                                             service_env_overrides):
    """Real checkout under a path containing spaces, incl. RUNTIME_DIR."""
    spaced_base = tmp_path / "WebAI to API integration"
    spaced_base.mkdir()
    from ._harness import IntegrationRepo

    repo = IntegrationRepo(spaced_base)
    assert " " in repo.work
    assert " " in repo.runtime_dir
    svc = service_env_overrides
    old_proc = _start_managed(repo, tracked_processes, svc)

    try:
        repo.remote_set_version("2.0")
        updated = repo.run_updater([], extra_env={
            "WEBAI_START_COMMAND": svc["start_command"],
            "WEBAI_HEALTH_URL": svc["url"],
        })
        assert updated.returncode == 0, updated.stderr

        new_pid = read_pid(repo)
        assert pid_alive(new_pid)
        assert 'version = "2.0"' in repo.read("pyproject.toml")
        assert repo.worktree_clean()
        assert wait_health(svc["url"], timeout=30)
        assert wait_process_exit(old_proc, timeout=20)
    finally:
        cleanup_managed_service(repo, svc["url"], svc["port"])
