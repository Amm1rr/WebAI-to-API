"""
End-to-end tests for scripts/update.py (Git-based updater).

Version trigger contract: `[project].version` inside pyproject.toml,
compared by equality between the local checkout and origin/master.

Each test builds a temporary origin (bare repo) plus two clones:
- `work`:   the installation the updater runs against; intentionally stays
            behind so its version differs from origin/master.
- `editor`: simulates upstream developers advancing origin/master.
"""

import http.server
import os
import socket
import subprocess
import sys
import tempfile
import time
import threading

import pytest

UPDATE_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "update.py",
)
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@test",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@test",
}


def git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True,
        env={**os.environ, **GIT_ENV},
    )


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _pid_from(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def make_pyproject(
    version="1.0",
    *,
    requires='>=3.11,<3.13',
    playwright="^1.60.0",
    httpx=">=0.28.1,<0.29.0",
    description="WebAI-to-API test package",
    project_deps=None,
    optional_deps=None,
    dep_groups=None,
    group_dev=None,
    omit_version=False,
):
    """Render a minimal but valid pyproject.toml for fixtures."""
    parts = [
        "[project]",
        'name = "webai-to-api"',
    ]
    if not omit_version:
        parts.append(f'version = "{version}"')
    parts.append(f'description = "{description}"')
    parts.append(f'requires-python = "{requires}"')
    if project_deps is not None:
        rendered = ", ".join(f'"{dep}"' for dep in project_deps)
        parts.append(f"dependencies = [{rendered}]")
    if optional_deps is not None:
        block = "\n".join(
            f'{group} = [{", ".join(chr(34) + d + chr(34) for d in deps)}]'
            for group, deps in optional_deps.items()
        )
        parts.append("[project.optional-dependencies]\n" + block)
    if dep_groups is not None:
        # PEP-735: dependency-groups is a table of arrays, not array-of-tables.
        block = "\n".join(
            f'{name} = [{", ".join(chr(34) + d + chr(34) for d in deps)}]'
            for name, deps in dep_groups.items()
        )
        parts.append("[dependency-groups]\n" + block)

    poetry_lines = ["[tool.poetry.dependencies]", f'python = "{requires}"']
    if playwright is not None:
        poetry_lines.append(f'playwright = "{playwright}"')
    if httpx is not None:
        poetry_lines.append(f'httpx = "{httpx}"')
    parts.append("\n".join(poetry_lines))

    if group_dev is not None:
        dev = ["[tool.poetry.group.dev.dependencies]"]
        dev += [f'{pkg} = "{constraint}"' for pkg, constraint in group_dev.items()]
        parts.append("\n".join(dev))
    return "\n".join(parts) + "\n"


class Repo:
    def __init__(self, tmp_path):
        self.base = tmp_path
        self.origin = str(tmp_path / "origin.git")
        self.work = str(tmp_path / "work")
        self.editor = str(tmp_path / "editor")
        self.poetry_bin = tmp_path / "bin"
        self.poetry_log = tmp_path / "poetry-calls.log"
        self.pid_file = tmp_path / "service.pid"
        self.log_file = tmp_path / "service.log"
        self.lock_file = tmp_path / "update.lock"
        self._build()

    def _build(self):
        subprocess.run(["git", "init", "--bare", "-b", "master", self.origin],
                       check=True, capture_output=True)
        for clone in (self.work, self.editor):
            subprocess.run(
                ["git", "clone", self.origin, clone],
                check=True, capture_output=True, env={**os.environ, **GIT_ENV},
            )
            git(clone, "checkout", "-b", "master")
        self.poetry_bin.mkdir(exist_ok=True)
        shim = self.poetry_bin / "poetry"
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$*" >> "{self.poetry_log}"\n'
            'exit ${FAKE_POETRY_EXIT:-0}\n'
        )
        shim.chmod(0o755)

        # c1: pre-pyproject installation (no version metadata at all).
        self._files_on_disk(self.work, {"app.txt": "one\n"})
        self.commit("initial")
        # c2: upstream publishes pyproject.toml with version 1.0;
        # work fast-forwards onto it.
        git(self.editor, "pull", "origin", "master")
        self._files_on_disk(self.editor, {
            "pyproject.toml": make_pyproject("1.0"),
            "poetry.lock": "# lock v1\n",
        })
        self.commit("add pyproject", target="editor")
        git(self.work, "pull", "origin", "master")

    def _files_on_disk(self, root, files):
        for name, content in files.items():
            path = os.path.join(root, name)
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w") as handle:
                handle.write(content)

    def write_files(self, files):
        self._files_on_disk(self.work, files)

    def commit(self, message, push=True, target="work", force_add=False):
        root = self.work if target == "work" else self.editor
        git(root, "add", "-A", "-f" if force_add else "-A")
        git(root, "commit", "--allow-empty", "-m", message)
        if push:
            git(root, "push", "origin", "master")

    def remote_bump(self, files=None, message="upstream update", force_add=False):
        """Advance origin/master via the editor clone; work clone untouched."""
        git(self.editor, "pull", "origin", "master")
        if files:
            self._files_on_disk(self.editor, files)
        self.commit(message, target="editor", force_add=force_add)

    def remote_set_version(self, version, **make_kwargs):
        """Publish a new [project].version while keeping dependency tables."""
        git(self.editor, "pull", "origin", "master")
        self._files_on_disk(self.editor, {
            "pyproject.toml": make_pyproject(version, **make_kwargs)
        })
        self.commit(f"release {version}", target="editor")

    def head(self):
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.work,
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def editor_head(self):
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.editor,
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def read(self, name):
        with open(os.path.join(self.work, name)) as handle:
            return handle.read()

    def poetry_calls(self):
        try:
            with open(self.poetry_log) as handle:
                return [line.strip() for line in handle if line.strip()]
        except OSError:
            return []

    def start_fake_service(self):
        process = subprocess.Popen(
            ["sleep", "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.pid_file.write_text(str(process.pid))
        return process.pid

    def env(self, **overrides):
        base = {**os.environ, **GIT_ENV}
        base.update({
            "WEBAI_ROOT": self.work,
            "WEBAI_PID_FILE": str(self.pid_file),
            "WEBAI_LOG_FILE": str(self.log_file),
            "WEBAI_LOCK_FILE": str(self.lock_file),
            "WEBAI_START_COMMAND": "sleep 30",
            "PATH": f"{self.poetry_bin}:{base.get('PATH', '')}",
            "POETRY_CALLS_LOG": str(self.poetry_log),
        })
        base.update({k: str(v) for k, v in overrides.items()})
        return base

    def run_updater(self, extra_env=None, timeout=90):
        return subprocess.run(
            [sys.executable, UPDATE_PY],
            capture_output=True, text=True, timeout=timeout,
            env=self.env(**(extra_env or {})),
        )

    def cleanup_pids(self, *pids):
        for pid in (*pids, _pid_from(self.pid_file)):
            if pid:
                subprocess.run(["kill", "-9", str(pid)], capture_output=True)


class FlakyHealth:
    """HTTP server whose /health returns 503 for the first N hits, then 200."""

    def __init__(self, fail_first=0):
        self.hits = 0
        self.fail_first = fail_first
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler_self):
                outer.hits += 1
                code = 200 if outer.hits > outer.fail_first else 503
                handler_self.send_response(code)
                handler_self.end_headers()

            def log_message(*_args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/health"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


# --- Version trigger contract -------------------------------------------


def test_same_project_version_is_noop(repo):
    result = repo.run_updater()
    assert result.returncode == 0
    assert "Already up to date" in result.stdout


def test_different_project_version_moves_head_to_origin_master(repo):
    repo.remote_set_version("2.0")
    expected_head = repo.editor_head()

    result = repo.run_updater()

    assert result.returncode == 0
    assert repo.head() == expected_head
    assert 'version = "2.0"' in repo.read("pyproject.toml")


def test_missing_local_pyproject_triggers_update(repo):
    # Rewind work to c1 (pre-pyproject history); c1 is an ancestor.
    first_commit = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=repo.work, check=True, capture_output=True, text=True,
    ).stdout.splitlines()[0]
    git(repo.work, "reset", "--hard", first_commit)
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert 'version = "2.0"' in repo.read("pyproject.toml")


def test_malformed_local_pyproject_still_updates(repo):
    # Work pinned on an intermediate upstream commit whose pyproject is
    # broken; tree stays clean, ancestry holds, local parse yields "".
    repo.remote_bump({"pyproject.toml": "NOT [VALID TOML"})
    broken_sha = repo.editor_head()
    repo.remote_set_version("2.0")
    git(repo.work, "fetch", "origin", "master")
    git(repo.work, "reset", "--hard", broken_sha)

    result = repo.run_updater()

    assert result.returncode == 0
    assert 'version = "2.0"' in repo.read("pyproject.toml")


def test_malformed_remote_pyproject_aborts_untouched(repo):
    repo.remote_bump({"pyproject.toml": "NOT [VALID TOML"}, force_add=False)
    local_before = repo.head()

    result = repo.run_updater()

    assert result.returncode != 0
    assert "No readable [project].version" in result.stderr
    assert repo.head() == local_before


def test_remote_pyproject_missing_version_aborts(repo):
    repo.remote_bump({"pyproject.toml": make_pyproject(omit_version=True)})
    local_before = repo.head()

    result = repo.run_updater()

    assert result.returncode != 0
    assert "No readable [project].version" in result.stderr
    assert repo.head() == local_before


# --- Preflight safety (unchanged contracts) ------------------------------


def test_fetch_failure_aborts_without_touching_worktree(repo):
    git(repo.work, "remote", "set-url", "origin", "/nonexistent/remote.git")
    head_before = repo.head()

    result = repo.run_updater()

    assert result.returncode != 0
    assert "git fetch failed" in result.stderr
    assert repo.head() == head_before


def test_dirty_tracked_file_aborts_and_preserves_file(repo):
    repo.remote_set_version("2.0")
    pyproject = os.path.join(repo.work, "pyproject.toml")
    with open(pyproject, "a") as handle:
        handle.write("\n# local edit\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "tracked/staged modifications" in result.stderr
    assert "local edit" in open(pyproject).read()


def test_local_commit_ahead_aborts(repo):
    repo.remote_set_version("2.0")
    repo.write_files({"app.txt": "local only\n"})
    repo.commit("local only", push=False)
    local_before = repo.head()

    result = repo.run_updater()

    assert result.returncode != 0
    assert "not on origin/master" in result.stderr
    assert repo.head() == local_before


def test_wrong_branch_aborts(repo):
    git(repo.work, "checkout", "-b", "feature")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "branch 'master'" in result.stderr


def test_protected_remote_path_aborts(repo):
    repo.remote_bump({".env": "EVIL=1\n"})
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "protected user-owned paths" in result.stderr


def test_untracked_collision_aborts_and_preserves_file(repo):
    repo.remote_set_version("2.0")
    repo.remote_bump({"newfile.txt": "remote\n"})
    collision = os.path.join(repo.work, "newfile.txt")
    with open(collision, "w") as handle:
        handle.write("precious local data\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "untracked/ignored local files" in result.stderr
    assert open(collision).read() == "precious local data\n"


def test_untracked_file_blocking_remote_directory_aborts(repo):
    repo.remote_set_version("2.0")
    repo.remote_bump({"foo/bar.py": "remote\n"})
    blocker = os.path.join(repo.work, "foo")
    with open(blocker, "w") as handle:
        handle.write("blocks dir creation\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert open(blocker).read() == "blocks dir creation\n"


def test_symlink_ancestor_collision_aborts(repo):
    repo.remote_set_version("2.0")
    repo.remote_bump({"foo/bar.py": "remote\n"})
    link = os.path.join(repo.work, "foo")
    target = os.path.join(repo.base, "outside-target.txt")
    with open(target, "w") as handle:
        handle.write("outside\n")
    os.symlink(target, link)

    result = repo.run_updater()

    assert result.returncode != 0
    assert os.path.islink(link)
    assert open(target).read() == "outside\n"


def test_plain_local_directory_allows_nested_remote_file(repo):
    repo.remote_set_version("2.0")
    os.makedirs(os.path.join(repo.work, "docs"), exist_ok=True)
    repo.remote_bump({"docs/guide.md": "guide\n"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert repo.read("docs/guide.md") == "guide\n"


# --- Dependency signature contract ---------------------------------------


def test_pure_version_bump_does_not_sync_dependencies(repo):
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert repo.poetry_calls() == []


def test_metadata_only_change_with_version_bump_does_not_sync(repo):
    repo.remote_set_version("2.0", description="brand new description text")

    result = repo.run_updater()

    assert result.returncode == 0
    assert repo.poetry_calls() == []


def test_poetry_dependency_change_triggers_sync(repo):
    repo.remote_set_version("2.0", playwright="^1.61.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())
    assert not any("playwright" in call for call in repo.poetry_calls())


def test_poetry_group_change_triggers_sync(repo):
    repo.remote_set_version("2.0", group_dev={"pytest": "^8.5"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())


def test_project_dependencies_change_triggers_sync(repo):
    repo.remote_set_version("2.0", project_deps=["httpx>=0.29"])

    result = repo.run_updater()

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())


def test_optional_dependencies_change_triggers_sync(repo):
    repo.remote_set_version(
        "2.0", optional_deps={"socks": ["aiohttp-socks>=0.11"]}
    )

    result = repo.run_updater()

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())


def test_dependency_groups_change_triggers_sync(repo):
    repo.remote_set_version("2.0", dep_groups={"dev": ["pytest>=8.4"]})

    result = repo.run_updater()

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())


def test_requires_python_change_triggers_sync(repo):
    repo.remote_set_version("2.0", requires=">=3.12,<3.13")

    result = repo.run_updater()

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())


def test_lock_only_change_with_version_bump_syncs_and_installs_playwright(repo):
    repo.remote_set_version("2.0")
    repo.remote_bump({"poetry.lock": "# changed lock\n"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())
    assert any("run playwright install chromium" in call
               for call in repo.poetry_calls())


def test_unparseable_old_pyproject_fails_safe_to_sync(repo):
    # Work pinned on an intermediate commit whose pyproject cannot be parsed:
    # signature comparison is impossible -> conservatively treat as changed.
    repo.remote_bump({"pyproject.toml": "NOT [VALID TOML"}, force_add=False)
    broken_sha = repo.editor_head()
    git(repo.work, "fetch", "origin", "master")
    git(repo.work, "reset", "--hard", broken_sha)
    repo.remote_set_version("2.0")

    result = repo.run_updater(extra_env={"FAKE_POETRY_EXIT": "0"})

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())


# --- Service lifecycle ----------------------------------------------------


def test_running_service_is_stopped_updated_restarted(repo):
    health = FlakyHealth(fail_first=0)
    old_pid = repo.start_fake_service()
    try:
        repo.remote_set_version("2.0")
        result = repo.run_updater(extra_env={
            "WEBAI_HEALTH_URL": health.url,
            "WEBAI_HEALTH_TIMEOUT": "5",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode == 0
        new_pid = _pid_from(repo.pid_file)
        assert new_pid != old_pid
        assert subprocess.run(["kill", "-0", str(new_pid)],
                              capture_output=True).returncode == 0
    finally:
        health.stop()
        repo.cleanup_pids(old_pid)


def test_previously_stopped_service_remains_stopped(repo):
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert not repo.pid_file.exists()


def test_health_failure_restores_previous_sha(repo):
    service_pid = repo.start_fake_service()
    previous_sha = repo.head()
    dead_port = _free_port()
    try:
        repo.remote_set_version("2.0")

        result = repo.run_updater(extra_env={
            "WEBAI_HEALTH_URL": f"http://127.0.0.1:{dead_port}/health",
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        assert "rolling back" in result.stderr.lower()
        assert repo.head() == previous_sha
        assert 'version = "1.0"' in repo.read("pyproject.toml")
    finally:
        repo.cleanup_pids(service_pid)


def test_dependency_failure_restores_previous_sha(repo):
    previous_sha = repo.head()
    lock_before = repo.read("poetry.lock")
    repo.remote_set_version("2.0", playwright="^9.9.9")

    result = repo.run_updater(extra_env={"FAKE_POETRY_EXIT": "3"})

    assert result.returncode != 0
    assert repo.head() == previous_sha
    assert repo.read("poetry.lock") == lock_before
    assert len([c for c in repo.poetry_calls() if "install --sync" in c]) >= 2


def test_rollback_restart_failure_is_fail_closed(repo):
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    try:
        repo.remote_set_version("2.0")
        result = repo.run_updater(extra_env={
            "WEBAI_START_COMMAND": "/nonexistent/start-binary",
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        combined = result.stdout + result.stderr
        # Legacy-compatible spawn wording survives platform translation.
        assert ("Cannot start service with "
                "'/nonexistent/start-binary': No such file or directory") \
            in combined
        assert "ROLLBACK FAILED" in result.stderr
        assert "left STOPPED" in result.stderr
        assert repo.head() == previous_sha
        assert "Traceback" not in result.stderr
        assert not repo.pid_file.exists()
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode != 0
    finally:
        repo.cleanup_pids(old_pid)


def test_log_open_failure_rolls_back_with_clean_error(repo):
    """Unwritable log path after code switch -> clean UpdateError + rollback."""
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    blocker = repo.base / "log-blocker"
    blocker.write_text("x")
    try:
        repo.remote_set_version("2.0")
        result = repo.run_updater(extra_env={
            "WEBAI_LOG_FILE": str(blocker / "service.log"),
            "WEBAI_HEALTH_URL": f"http://127.0.0.1:{_free_port()}/health",
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert ("Cannot start service with 'sleep 30': "
                "Not a directory") in combined
        assert "Traceback" not in result.stderr
        assert repo.head() == previous_sha
        assert 'version = "1.0"' in repo.read("pyproject.toml")
    finally:
        repo.cleanup_pids(old_pid)
        if blocker.exists():
            blocker.unlink()


# --- Locking, stop command, guards ----------------------------------------


def test_stop_command_stops_running_service(repo):
    old_pid = repo.start_fake_service()
    try:
        result = repo.run_updater_extra(["--stop"]) if hasattr(
            repo, "run_updater_extra"
        ) else subprocess.run(
            [sys.executable, UPDATE_PY, "--stop"],
            capture_output=True, text=True, timeout=60, env=repo.env(),
        )

        assert result.returncode == 0
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode != 0
        assert not repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_stale_pid_file_cleaned_by_stop_command(repo):
    repo.pid_file.write_text("999999999")

    result = subprocess.run(
        [sys.executable, UPDATE_PY, "--stop"],
        capture_output=True, text=True, timeout=60, env=repo.env(),
    )

    assert result.returncode == 0
    assert "stale PID file" in result.stdout
    assert not repo.pid_file.exists()


def test_normal_flow_leaves_no_stale_pid_file_when_stopped(repo):
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert not repo.pid_file.exists()


def test_updater_lock_blocks_second_instance(repo):
    import fcntl
    fd = os.open(repo.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        result = repo.run_updater()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert result.returncode == 0
    assert "Another update operation or update check" in result.stderr
    assert repo.lock_file.exists()


def test_lock_released_after_run(repo):
    assert repo.run_updater().returncode == 0
    assert repo.run_updater().returncode == 0


def test_stop_waits_for_active_updater_lock(repo):
    import fcntl
    old_pid = repo.start_fake_service()
    fd = os.open(repo.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = subprocess.run(
            [sys.executable, UPDATE_PY, "--stop"],
            capture_output=True, text=True, timeout=60, env=repo.env(),
        )
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

        assert result.returncode == 0
        assert "Another update operation or update check" in result.stderr
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode == 0
        assert repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_preflight_failures_have_no_traceback(repo):
    git(repo.work, "checkout", "-b", "feature")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("ERROR: ")


def test_staged_tracked_change_aborts(repo):
    repo.remote_set_version("2.0")
    repo._files_on_disk(repo.work, {"app.txt": "staged-change\n"})
    git(repo.work, "add", "-A")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "tracked/staged modifications" in result.stderr


def test_detached_head_aborts(repo):
    sha = repo.head()
    git(repo.work, "checkout", "--detach", sha)

    result = repo.run_updater()

    assert result.returncode != 0
    assert "detached HEAD" in result.stderr


def test_example_files_are_not_protected(repo):
    repo.remote_bump({
        ".env.example": "ATLASCLOUD_API_KEY=\n",
        "config.conf.example": "[Gemini]\n",
    })
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert os.path.exists(os.path.join(repo.work, ".env.example"))
    assert os.path.exists(os.path.join(repo.work, "config.conf.example"))


def test_runtime_subtree_is_blocked(repo):
    repo.remote_bump({"runtime/keep.db": "state\n"}, force_add=True)
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "protected user-owned paths" in result.stderr
    assert not os.path.exists(os.path.join(repo.work, "runtime"))


def test_nested_untracked_collision_aborts_and_preserves(repo):
    repo.remote_set_version("2.0")
    repo.remote_bump({"pkg/mod.py": "remote content\n"})
    local = os.path.join(repo.work, "pkg", "mod.py")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    with open(local, "w") as handle:
        handle.write("precious\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert open(local).read() == "precious\n"


def test_ignored_file_collision_aborts_and_preserves(repo):
    repo.write_files({".gitignore": "logs/\n"})
    repo.commit("ignore logs", push=True)
    repo.remote_bump({"logs/app.log": "remote log\n"}, force_add=True)
    repo.remote_set_version("2.0")
    ignored = os.path.join(repo.work, "logs", "app.log")
    os.makedirs(os.path.dirname(ignored), exist_ok=True)
    with open(ignored, "w") as handle:
        handle.write("precious local log\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert open(ignored).read() == "precious local log\n"


def test_non_colliding_ignored_files_remain_allowed(repo):
    repo.write_files({".gitignore": "logs/\n"})
    repo.commit("ignore logs", push=True)
    logs_dir = os.path.join(repo.work, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    with open(os.path.join(logs_dir, "other.log"), "w") as handle:
        handle.write("kept\n")
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert open(os.path.join(logs_dir, "other.log")).read() == "kept\n"


def test_rollback_dep_restoration_failure_leaves_service_stopped(repo):
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    try:
        repo.remote_set_version("2.0", playwright="^9.9.9")
        result = repo.run_updater(extra_env={"FAKE_POETRY_EXIT": "3"})

        assert result.returncode != 0
        assert "ROLLBACK FAILED" in result.stderr
        assert "left STOPPED" in result.stderr
        assert repo.head() == previous_sha
        assert not repo.pid_file.exists()
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode != 0
    finally:
        repo.cleanup_pids(old_pid)


def test_missing_poetry_executable_rolls_back_with_clean_error(repo):
    previous_sha = repo.head()
    lock_before = repo.read("poetry.lock")
    repo.remote_set_version("2.0", playwright="^9.9.9")

    result = repo.run_updater(extra_env={
        "WEBAI_POETRY": "/nonexistent/poetry-missing",
    })

    assert result.returncode != 0
    assert "Cannot execute '/nonexistent/poetry-missing'" in result.stderr
    assert repo.head() == previous_sha
    assert repo.read("poetry.lock") == lock_before


def test_malformed_start_command_is_fail_closed_rollback(repo):
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    try:
        repo.remote_set_version("2.0")
        result = repo.run_updater(extra_env={
            'WEBAI_START_COMMAND': 'poetry run "unclosed quote',
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        assert "ROLLBACK FAILED" in result.stderr
        assert "left STOPPED" in result.stderr
        assert repo.head() == previous_sha
        assert "Traceback" not in result.stderr
        assert not repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_empty_start_command_is_fail_closed_rollback(repo):
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    try:
        repo.remote_set_version("2.0")
        result = repo.run_updater(extra_env={
            "WEBAI_START_COMMAND": "",
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        assert "ROLLBACK FAILED" in result.stderr
        assert "left STOPPED" in result.stderr
        assert repo.head() == previous_sha
    finally:
        repo.cleanup_pids(old_pid)


def test_lock_file_open_failure_is_clean_error(repo):
    blocker = os.path.join(repo.base, "lock-blocker")
    with open(blocker, "w") as handle:
        handle.write("x")

    result = repo.run_updater(extra_env={
        "WEBAI_LOCK_FILE": os.path.join(blocker, "child.lock"),
    })

    assert result.returncode != 0
    assert "Cannot open updater lock file" in result.stderr
    assert "Traceback" not in result.stderr


def test_unexpected_flock_error_raises_platform_error(tmp_path, monkeypatch):
    """Non-contention flock failures surface as PlatformOperationError."""
    import errno
    import importlib.util
    platform_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "update_platform.py",
    )
    spec = importlib.util.spec_from_file_location("update_pflock", platform_path)
    platform_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(platform_module)

    def boom(fd, operation):
        raise OSError(errno.EIO, "Simulated I/O error")

    monkeypatch.setattr(platform_module.fcntl, "flock", boom)
    with pytest.raises(
        platform_module.PlatformOperationError
    ) as excinfo:
        platform_module.acquire_lock(str(tmp_path / "update.lock"))
    assert getattr(excinfo.value, "phase", "") == "flock"


def test_tracked_symlink_in_head_remains_allowed(repo):
    git(repo.editor, "pull", "origin", "master")
    link = os.path.join(repo.editor, "shim")
    os.symlink("app.txt", link)
    repo.commit("add tracked symlink", target="editor")
    git(repo.work, "pull", "origin", "master")
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert os.path.islink(os.path.join(repo.work, "shim"))


def test_docker_stop_with_stale_pid_cleans_up(repo, monkeypatch):
    repo.pid_file.write_text("999999999")
    module = _load_update_module_for(repo, monkeypatch)

    rc = module.main(["--stop"])

    assert rc == 0
    assert not repo.pid_file.exists()


def test_container_guard_refuses():
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_mod", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(module.UpdateError, match="docker compose up -d --build"):
        module.container_guard(exists=lambda p: p == "/.dockerenv")
    module.container_guard(exists=lambda p: False)


def _load_update_module_for(repo, monkeypatch=None):
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_docker", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env = repo.env()
    module.ROOT = env["WEBAI_ROOT"]
    module.PID_FILE = env["WEBAI_PID_FILE"]
    module.LOG_FILE = env["WEBAI_LOG_FILE"]
    module.LOCK_FILE = env["WEBAI_LOCK_FILE"]

    if monkeypatch is not None:
        real_exists = os.path.exists

        def fake_exists(path):
            if path == "/.dockerenv":
                return True
            return real_exists(path)

        monkeypatch.setattr(os.path, "exists", fake_exists)
    return module


def test_docker_normal_update_is_refused(repo, monkeypatch, capsys):
    repo.remote_set_version("2.0")
    head_before = repo.head()
    module = _load_update_module_for(repo, monkeypatch)

    rc = module.main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert "host installations only" in captured.err
    assert repo.head() == head_before
    assert not repo.pid_file.exists()


def test_docker_stop_with_running_service_succeeds(repo, monkeypatch):
    old_pid = repo.start_fake_service()
    try:
        module = _load_update_module_for(repo, monkeypatch)
        rc = module.main(["--stop"])

        assert rc == 0
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode != 0
        assert not repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_docker_stop_blocked_by_active_updater_lock(repo, monkeypatch, capsys):
    import fcntl
    old_pid = repo.start_fake_service()
    fd = os.open(repo.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        module = _load_update_module_for(repo, monkeypatch)
        rc = module.main(["--stop"])
        captured = capsys.readouterr()

        assert rc == 0
        assert "Another update operation or update check" in captured.err
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode == 0
        assert repo.pid_file.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        repo.cleanup_pids(old_pid)


# --- Phase 2 extraction seams -----------------------------------------------


def _load_bound_update_module(repo):
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_seam", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = repo.env()
    module.ROOT = env["WEBAI_ROOT"]
    module.PID_FILE = env["WEBAI_PID_FILE"]
    module.LOG_FILE = env["WEBAI_LOG_FILE"]
    return module


def test_stop_ordering_terminate_wait_then_force(repo, monkeypatch):
    """Grace expiry: terminate -> wait -> force_kill, then PID file removed."""
    module = _load_update_module_for(repo)
    old_pid = repo.start_fake_service()
    events = []

    def fake_alive(pid):
        return True  # process refuses to die within the grace window

    monkeypatch.setattr(module, "platform_pid_alive", fake_alive)
    monkeypatch.setattr(
        module, "platform_terminate_graceful",
        lambda pid: events.append(("terminate", pid)),
    )
    monkeypatch.setattr(
        module, "platform_force_kill",
        lambda pid: events.append(("force", pid)),
    )
    monkeypatch.setattr(module.time, "sleep",
                        lambda s: events.append(("sleep", s)))

    module.stop_service(old_pid)

    kinds = [event[0] for event in events]
    assert kinds[0] == "terminate"
    assert "force" in kinds
    assert kinds.index("terminate") < kinds.index("force")
    assert all(event[0] != "terminate" or i == 0
               for i, event in enumerate(events))
    assert kinds.count("terminate") == 1
    assert not repo.pid_file.exists()
    repo.cleanup_pids(old_pid)


def test_stop_graceful_exit_skips_force_kill(repo, monkeypatch):
    module = _load_update_module_for(repo)
    old_pid = repo.start_fake_service()
    calls = []
    state = {"alive": True}

    def fake_alive(pid):
        alive = state["alive"]
        state["alive"] = False
        return alive

    monkeypatch.setattr(module, "platform_pid_alive", fake_alive)
    monkeypatch.setattr(module, "platform_terminate_graceful",
                        lambda pid: calls.append("terminate"))
    monkeypatch.setattr(module, "platform_force_kill",
                        lambda pid: calls.append("force"))

    module.stop_service(old_pid)

    assert calls == ["terminate"]
    assert not repo.pid_file.exists()
    repo.cleanup_pids(old_pid)


def test_main_releases_acquired_lock_exactly_once(repo, monkeypatch):
    module = _load_update_module_for(repo)
    releases = []

    original_release = __import__("update_platform").LockHandle.release

    def spy_release(self):
        if self._released:
            return
        releases.append(True)
        original_release(self)

    monkeypatch.setattr(
        __import__("update_platform").LockHandle, "release", spy_release
    )

    repo.remote_set_version("2.0") if hasattr(repo, "remote_set_version") else None
    assert module.main([]) == 0
    assert len(releases) == 1


# --- Phase 5: Windows updater policy (semantics mocked; real Windows = 6) ---


class _FakeClock:
    """Scripted time.monotonic/sleep pair: sleep advances the clock."""

    def __init__(self, start=1000.0):
        self.start = start
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _windows_stop_module(repo, monkeypatch, ipc_results, alive_sequence):
    """Bound updater module forced onto the Windows stop path.

    ipc_results: consumed per IPC call ("ok"/"retry"/"unreachable"/Exception).
    alive_sequence: liveness polls; last value repeats once exhausted.
    Time is faked: sleep advances a monotonic clock, so no real waiting.
    Returns (module, ipc_calls, force_calls, ipc_timeouts, clock).
    """
    module = _load_update_module_for(repo)
    module.IS_WINDOWS = True
    clock = _FakeClock()
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(time, "sleep", clock.sleep)

    import importlib
    transport_error_cls = (
        importlib.import_module("app.shutdown_transport")
        .ShutdownTransportError
    )
    monkeypatch.setattr(
        module, "ShutdownTransportError", transport_error_cls, raising=False
    )

    ipc_calls = []
    ipc_timeouts = []

    def fake_ipc(control_file, timeout=None):
        result = (
            ipc_results.pop(0) if ipc_results else "unreachable"
        )
        if isinstance(result, Exception):
            raise result
        ipc_calls.append(result)
        ipc_timeouts.append(timeout)
        return result

    # Production seam: request_service_shutdown resolves this lazily.
    monkeypatch.setattr(module, "_SEND_SHUTDOWN", fake_ipc)

    alive_calls = []

    def fake_alive(pid):
        state = (
            alive_sequence.pop(0)
            if len(alive_sequence) > 1
            else alive_sequence[0]
        )
        alive_calls.append(state)
        return state

    force_calls = []
    monkeypatch.setattr(module, "platform_pid_alive", fake_alive)
    monkeypatch.setattr(
        module,
        "platform_force_kill",
        lambda pid: force_calls.append(pid),
    )
    return module, ipc_calls, force_calls, ipc_timeouts, clock


def test_windows_stop_ipc_ok_no_repeat_no_force(repo, monkeypatch, capsys):
    old_pid = repo.start_fake_service()
    try:
        # tick1 alive+ok, tick2 dead -> success without force.
        module, ipc_calls, force_calls, timeouts, clock = (
            _windows_stop_module(
                repo, monkeypatch,
                ipc_results=["ok"],
                alive_sequence=[True, False],
            )
        )

        module.stop_service(4321)
        captured = capsys.readouterr()  # read the buffer exactly once

        assert ipc_calls == ["ok"]           # sent exactly once after "ok"
        assert len(timeouts) == 1
        assert timeouts[0] <= module.WINDOWS_IPC_TIMEOUT_SECONDS
        assert force_calls == []             # graceful acceptance honored
        assert "forcing termination" not in captured.out
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_retry_then_ok_then_exit(repo, monkeypatch, capsys):
    old_pid = repo.start_fake_service()
    try:
        module, ipc_calls, force_calls, timeouts, clock = (
            _windows_stop_module(
                repo, monkeypatch,
                ipc_results=["retry", "retry", "ok"],
                alive_sequence=[True, True, True, False],
            )
        )

        module.stop_service(4321)

        assert ipc_calls == ["retry", "retry", "ok"]
        assert force_calls == []
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_unreachable_budget_then_force(repo, monkeypatch):
    old_pid = repo.start_fake_service()
    try:
        module, ipc_calls, force_calls, timeouts, clock = (
            _windows_stop_module(
                repo, monkeypatch,
                ipc_results=[],               # never reachable
                alive_sequence=[True],        # stays alive whole budget
            )
        )

        module.stop_service(4321)

        assert len(ipc_calls) == 10       # one attempt per grace tick
        assert force_calls == [4321]      # fallback engaged exactly once
        elapsed = clock.now - clock.start
        assert elapsed <= module.WINDOWS_STOP_BUDGET_SECONDS + 1e-9
        assert all(t is not None and t <= 3.0 for t in timeouts)
        assert timeouts[-1] < timeouts[0]  # cap shrinks near deadline
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_stale_metadata_behaves_as_unreachable(
    repo, monkeypatch,
):
    """Malformed/stale control metadata is retryable, then hard fallback."""
    old_pid = repo.start_fake_service()
    try:
        module, ipc_calls, force_calls, _timeouts, _clock = (
            _windows_stop_module(
            repo, monkeypatch,
            ipc_results=[
                app_shutdown_transport_error(),
                    app_shutdown_transport_error(),
                    "retry",
                ],
                alive_sequence=[True],
            )
        )

        module.stop_service(4321)

        assert len(force_calls) == 1
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_process_exits_during_retries(repo, monkeypatch):
    old_pid = repo.start_fake_service()
    try:
        module, ipc_calls, force_calls, _timeouts, _clock = (
            _windows_stop_module(
            repo, monkeypatch,
                ipc_results=["retry"],
                alive_sequence=[True, False],
            )
        )

        module.stop_service(4321)

        assert force_calls == []          # exit beats any IPC outcome
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_already_dead_no_ipc_no_force(repo, monkeypatch):
    old_pid = repo.start_fake_service()
    try:
        module, ipc_calls, force_calls, _timeouts, _clock = (
            _windows_stop_module(
            repo, monkeypatch,
                ipc_results=["ok"],
                alive_sequence=[False],
            )
        )

        module.stop_service(4321)

        assert ipc_calls == []
        assert force_calls == []
    finally:
        repo.cleanup_pids(old_pid)


def app_shutdown_transport_error():
    import importlib
    cls = (
        importlib.import_module("app.shutdown_transport")
        .ShutdownTransportError
    )
    return cls("stale control file")


def test_windows_temp_defaults_use_gettempdir(monkeypatch, tmp_path):
    import importlib.util

    monkeypatch.setenv("WEBAI_PID_FILE", "")
    monkeypatch.setenv("WEBAI_LOG_FILE", "")
    monkeypatch.setenv("WEBAI_LOCK_FILE", "")
    monkeypatch.delenv("WEBAI_PID_FILE", raising=False)
    monkeypatch.delenv("WEBAI_LOG_FILE", raising=False)
    monkeypatch.delenv("WEBAI_LOCK_FILE", raising=False)
    spec = importlib.util.spec_from_file_location("update_nt_paths", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # On POSIX CI the defaults must remain byte-identical /tmp literals...
    assert module.PID_FILE == "/tmp/webai-to-api.pid"
    assert module.LOCK_FILE == "/tmp/webai-to-api-update.lock"

    # ...while the Windows base helper routes through gettempdir().
    monkeypatch.setattr(module, "IS_WINDOWS", True)
    assert module._temp_base() == tempfile.gettempdir()
    expected = os.path.join(tempfile.gettempdir(), "webai-to-api.pid")
    helper_default = os.path.join(module._temp_base(), "webai-to-api.pid")
    assert helper_default == expected


def test_env_path_overrides_win(repo):
    module = _load_bound_update_module(repo)
    env = repo.env()
    assert module.PID_FILE == env["WEBAI_PID_FILE"]
    assert module.LOG_FILE == env["WEBAI_LOG_FILE"]


def test_windows_quoted_path_parsing_preserves_backslashes(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_parse_win", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.IS_WINDOWS = True
    object.__setattr__(module, "START_COMMAND",
                       '"C:\\Program Files\\Poetry\\poetry.exe" run python src/run.py')
    monkeypatch.setattr(
        module.shutil, "which",
        lambda name: "C:\\Resolved\\poetry.exe" if "poetry" in name else None,
    )

    argv = module.parse_start_command()

    assert argv[0] == "C:\\Resolved\\poetry.exe"
    assert argv[1:] == ["run", "python", "src/run.py"]


def test_windows_executable_resolution_failure_clean(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_parse_bad", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.IS_WINDOWS = True
    object.__setattr__(module, "START_COMMAND", "missing-tool serve")
    monkeypatch.setattr(module.shutil, "which", lambda name: None)

    with pytest.raises(module.UpdateError, match="Cannot resolve executable"):

        module.parse_start_command()


def test_posix_parsing_unchanged(repo):
    module = _load_bound_update_module(repo)
    object.__setattr__(module, "START_COMMAND",
                       "poetry run python src/run.py")
    assert module.parse_start_command() == [
        "poetry", "run", "python", "src/run.py",
    ]


def test_rollback_restart_uses_windows_spawn_path(repo, monkeypatch):
    """Windows rollback restart resolves argv and passes detached spawn."""
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    spawned = {}
    real_platform_spawn = None

    class FakePopen:
        def __init__(self, pid):
            self.pid = pid

        def kill(self):
            pass

    try:
        module = _load_update_module_for(repo)
        module.IS_WINDOWS = True
        module.START_COMMAND = "sleep 30"
        module.HEALTH_TIMEOUT = 1
        module.HEALTH_INTERVAL = 0.1
        module.SHUTDOWN_CONTROL_FILE = str(repo.base / "shutdown-control.json")
        health_results = iter([False, True])
        monkeypatch.setattr(
            module,
            "wait_for_health",
            lambda *_args, **_kwargs: next(health_results),
        )

        def fake_spawn(argv, cwd, log_handle):
            spawned["argv"] = list(argv)
            spawned["cwd"] = cwd
            return FakePopen(old_pid)

        monkeypatch.setattr(module, "platform_spawn_detached", fake_spawn)
        monkeypatch.setattr(module.shutil, "which",
                            lambda name: "/usr/bin/sleep" if name == "sleep" else None)
        repo.remote_set_version("2.0")

        with pytest.raises(SystemExit):
            module.main([])  # health gate will fail -> rollback path exits

        assert spawned["cwd"] == module.ROOT
        assert spawned["argv"][0] == "/usr/bin/sleep"  # Windows-resolved argv
        # Rollback actually executed and restored the previous release.
        assert repo.head() == previous_sha
        assert 'version = "1.0"' in repo.read("pyproject.toml")
    finally:
        repo.cleanup_pids(old_pid)


# --- update-windows.cmd wrapper contract (textual; real execution = Phase 6) -


def test_update_windows_wrapper_contract():
    wrapper_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "update-windows.cmd"
    )
    with open(wrapper_path, encoding="utf-8") as handle:
        content = handle.read()

    # Repo-root cd + argument forwarding.
    assert 'cd /d "%~dp0"' in content
    assert "%*" in content

    # Execution-time ERRORLEVEL dispatch (stale %errorlevel% is forbidden).
    assert "if errorlevel 1 goto fallback" in content
    assert "if not errorlevel 1 goto run312" in content
    assert "if not errorlevel 1 goto run311" in content
    assert ":run312" in content and ":run311" in content
    assert ":fallback" in content
    assert "%errorlevel%==0" not in content  # stale percent-expansion guard

    # Both supported launcher versions probed; python is the final fallback;
    # each path runs the updater exactly once and propagates its rc.
    assert 'py -3.12 -c "import sys" >nul 2>nul' in content
    assert 'py -3.11 -c "import sys" >nul 2>nul' in content
    for interpreter in ("py -3.12", "py -3.11", "python"):
        invocation = f"{interpreter} scripts\\update.py %*"
        assert content.count(invocation) == 1
    assert content.count("exit /b %errorlevel%") == 3

    # No retry chains, no PowerShell, no delayed expansion machinery.
    assert "||" not in content
    assert "powershell" not in content.lower()
    assert "setlocal enabledelayedexpansion" not in content.lower()
    assert "!errorlevel!" not in content
