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
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import time
import threading
import urllib.request

import pytest

UPDATE_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "update.py",
)
SCRIPTS_DIR = os.path.dirname(UPDATE_PY)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from update_platform import (  # noqa: E402
    PlatformOperationError,
    force_kill as platform_force_kill,
    pid_alive as platform_pid_alive,
)

POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="POSIX-only updater mechanics"
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


class _LoopbackHTTPServer(http.server.HTTPServer):
    """HTTPServer variant that never reverse-resolves loopback."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


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
        self.poetry_cmd = self.poetry_bin / "poetry.cmd"
        self.poetry_log = tmp_path / "poetry-calls.log"
        self.pid_file = tmp_path / "service.pid"
        self.log_file = tmp_path / "service.log"
        self.lock_file = tmp_path / "update.lock"
        self.start_command = (
            f'"{sys.executable}" -c "import time; time.sleep(30)"'
        )
        self._processes = {}
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
        if os.name == "nt":
            shim = self.poetry_cmd
            shim.write_text(
                "@echo off\n"
                f'"{sys.executable}" -c "'
                "import os, sys; "
                "open(os.environ['POETRY_CALLS_LOG'], 'a').write("
                "' '.join(sys.argv[1:]) + '\\n'); "
                "raise SystemExit(int(os.environ.get('FAKE_POETRY_EXIT', '0')))"
                '" %*\n'
                "exit /b %errorlevel%\n"
            )
        else:
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
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._processes[process.pid] = process
        self.pid_file.write_text(str(process.pid))
        return process.pid

    def env(self, **overrides):
        base = {**os.environ, **GIT_ENV}
        base.update({
            "WEBAI_ROOT": self.work,
            "WEBAI_PID_FILE": str(self.pid_file),
            "WEBAI_LOG_FILE": str(self.log_file),
            "WEBAI_LOCK_FILE": str(self.lock_file),
            "WEBAI_START_COMMAND": self.start_command,
            "PATH": os.pathsep.join((str(self.poetry_bin), base.get("PATH", ""))),
            "POETRY_CALLS_LOG": str(self.poetry_log),
        })
        if os.name == "nt":
            base["WEBAI_POETRY"] = str(self.poetry_cmd)
        base.update({k: str(v) for k, v in overrides.items()})
        return base

    def run_updater(self, extra_env=None, timeout=90):
        return subprocess.run(
            [sys.executable, UPDATE_PY],
            capture_output=True, text=True, timeout=timeout,
            env=self.env(**(extra_env or {})),
        )

    def cleanup_pids(self, *pids):
        candidates = (*pids, _pid_from(self.pid_file))
        for pid in dict.fromkeys(pid for pid in candidates if pid):
            process = self._processes.get(pid)
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                continue
            if platform_pid_alive(pid):
                try:
                    platform_force_kill(pid)
                except Exception:
                    pass

    def pid_alive(self, pid):
        process = self._processes.get(pid)
        if process is not None:
            return process.poll() is None
        return platform_pid_alive(pid)


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

        self.httpd = _LoopbackHTTPServer(("127.0.0.1", 0), Handler)
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


def test_flaky_health_does_not_reverse_resolve_loopback(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getfqdn",
        lambda *_args: pytest.fail("loopback health server used reverse DNS"),
    )
    health = FlakyHealth()
    try:
        with urllib.request.urlopen(health.url, timeout=2) as response:
            assert response.status == 200
        port = int(health.url.rsplit(":", 1)[1].split("/", 1)[0])
        assert health.httpd.server_port == port
    finally:
        health.stop()


def test_repo_env_prepends_fake_poetry_with_host_separator(repo):
    assert repo.env()["PATH"].startswith(f"{repo.poetry_bin}{os.pathsep}")


def test_windows_repo_env_uses_explicit_fake_poetry_path(repo, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert repo.env()["WEBAI_POETRY"] == str(repo.poetry_cmd)


@pytest.mark.skipif(os.name != "nt", reason="Windows fake Poetry contract")
def test_windows_fake_poetry_records_args_and_exit_code(repo):
    repo.remote_set_version("2.0", playwright="^9.9.9")

    result = repo.run_updater(extra_env={"FAKE_POETRY_EXIT": "3"})

    assert result.returncode != 0
    assert "Command failed (3)" in result.stderr
    assert any("install --sync" in call for call in repo.poetry_calls())


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


@POSIX_ONLY
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


@pytest.mark.parametrize(
    ("new_kwargs", "changed"),
    [
        ({"description": "brand new description text"}, False),
        ({"playwright": "^1.61.0"}, True),
        ({"group_dev": {"pytest": "^8.5"}}, True),
        ({"project_deps": ["httpx>=0.29"]}, True),
        ({"optional_deps": {"socks": ["aiohttp-socks>=0.11"]}}, True),
        ({"dep_groups": {"dev": ["pytest>=8.4"]}}, True),
        ({"requires": ">=3.12,<3.13"}, True),
    ],
)
def test_dependency_signature_change_decision(new_kwargs, changed):
    module = _fresh_update_module("update_signature_unit")
    old = module._dependency_signature(make_pyproject("1.0"))
    new = module._dependency_signature(make_pyproject("2.0", **new_kwargs))

    assert (old != new) is changed


def test_poetry_dependency_change_triggers_sync(repo):
    repo.remote_set_version("2.0", playwright="^1.61.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())
    assert not any("playwright" in call for call in repo.poetry_calls())


def test_unparseable_dependency_signature_fails_safe_to_changed():
    module = _fresh_update_module("update_signature_invalid")

    assert module._dependency_signature("NOT [VALID TOML") is None


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


def test_running_service_is_stopped_updated_restarted(repo, monkeypatch):
    """Orchestration contract: stop old -> switch -> start new -> healthy.
    Stop/spawn/health mechanics themselves are owned by the dedicated
    platform and integration suites; this asserts the state transitions."""
    module = _load_update_module_for(repo)
    old_pid = 4242
    _publish_pid(module, old_pid)
    state = _fake_service_lifecycle(module, monkeypatch, [old_pid])
    starts = []

    def fake_start():
        starts.append(True)
        module._write_pid_file(5151)

    monkeypatch.setattr(module, "start_service", fake_start)
    monkeypatch.setattr(module, "wait_for_health", lambda *_args: True)
    repo.remote_set_version("2.0")

    assert module.main([]) == 0

    assert state["terminated"] == [old_pid]
    assert starts == [True]
    assert _pid_from(repo.pid_file) == 5151


def test_previously_stopped_service_remains_stopped(repo, monkeypatch):
    module = _load_update_module_for(repo)
    monkeypatch.setattr(module, "stop_service", _forbidden("stop_service"))
    repo.remote_set_version("2.0")

    assert module.main([]) == 0

    assert not repo.pid_file.exists()


def test_health_failure_restores_previous_sha(repo, monkeypatch, capsys):
    """Health-gate failure after a switched+started update triggers the full
    rollback decision: previous release restored, updater exits nonzero."""
    module = _load_update_module_for(repo)
    previous_sha = repo.head()
    old_pid = 4242
    _publish_pid(module, old_pid)
    state = _fake_service_lifecycle(module, monkeypatch, [old_pid])
    starts = []

    def failing_start():
        starts.append(True)
        state["alive"][5151] = True
        module._write_pid_file(5151)

    monkeypatch.setattr(module, "start_service", failing_start)
    monkeypatch.setattr(module, "wait_for_health", lambda *_args: False)
    repo.remote_set_version("2.0")

    code = _run_main_expecting_exit(module)

    assert code == 1
    # Unhealthy instance from the failed update is stopped before restore.
    assert state["terminated"] == [old_pid, 5151]
    assert len(starts) == 2  # post-update attempt + rollback restart
    assert repo.head() == previous_sha
    assert 'version = "1.0"' in repo.read("pyproject.toml")
    assert "update failed; rolling back" in capsys.readouterr().err.lower()


def test_dependency_failure_restores_previous_sha(repo, monkeypatch):
    module = _load_update_module_for(repo)
    module.POETRY_COMMAND = str(
        repo.poetry_cmd if os.name == "nt" else repo.poetry_bin / "poetry"
    )
    monkeypatch.setenv("FAKE_POETRY_EXIT", "3")
    monkeypatch.setenv("POETRY_CALLS_LOG", str(repo.poetry_log))
    previous_sha = repo.head()
    lock_before = repo.read("poetry.lock")
    repo.remote_set_version("2.0", playwright="^9.9.9")

    code = _run_main_expecting_exit(module)

    assert code == 1
    assert repo.head() == previous_sha
    assert repo.read("poetry.lock") == lock_before
    assert len([c for c in repo.poetry_calls() if "install --sync" in c]) >= 2


def test_rollback_dep_restoration_failure_leaves_service_stopped(
    repo, monkeypatch, capsys,
):
    module = _load_update_module_for(repo)
    module.POETRY_COMMAND = str(
        repo.poetry_cmd if os.name == "nt" else repo.poetry_bin / "poetry"
    )
    monkeypatch.setenv("FAKE_POETRY_EXIT", "3")
    monkeypatch.setenv("POETRY_CALLS_LOG", str(repo.poetry_log))
    old_pid = 4242
    _publish_pid(module, old_pid)
    state = _fake_service_lifecycle(module, monkeypatch, [old_pid])
    previous_sha = repo.head()
    repo.remote_set_version("2.0", playwright="^9.9.9")

    code = _run_main_expecting_exit(module)

    assert code == 1
    captured = capsys.readouterr()
    assert "ROLLBACK FAILED" in captured.err
    assert "left STOPPED" in captured.err
    assert state["terminated"] == [old_pid]
    assert not state["alive"][old_pid]
    assert repo.head() == previous_sha
    assert not repo.pid_file.exists()


def test_rollback_restart_failure_is_fail_closed(repo, monkeypatch, capsys):
    module = _load_update_module_for(repo)
    module.START_COMMAND = "/nonexistent/start-binary"
    old_pid = 4242
    _publish_pid(module, old_pid)
    state = _fake_service_lifecycle(module, monkeypatch, [old_pid])
    previous_sha = repo.head()
    repo.remote_set_version("2.0")

    code = _run_main_expecting_exit(module)

    assert code == 1
    combined = capsys.readouterr()
    text = combined.out + combined.err
    assert "'/nonexistent/start-binary'" in text
    assert (
        "Cannot start service with" in text
        or "Cannot resolve executable" in text
    )
    assert "ROLLBACK FAILED" in combined.err
    assert "left STOPPED" in combined.err
    assert repo.head() == previous_sha
    assert "Traceback" not in combined.err
    assert not repo.pid_file.exists()
    assert not state["alive"][old_pid]


def test_log_open_failure_rolls_back_with_clean_error(repo, monkeypatch, capsys):
    """Unwritable log path after code switch -> clean UpdateError + rollback."""
    module = _load_update_module_for(repo)
    blocker = repo.base / "log-blocker"
    blocker.write_text("x")
    module.LOG_FILE = str(blocker / "service.log")
    old_pid = 4242
    _publish_pid(module, old_pid)
    _fake_service_lifecycle(module, monkeypatch, [old_pid])
    previous_sha = repo.head()
    try:
        repo.remote_set_version("2.0")

        code = _run_main_expecting_exit(module)

        assert code == 1
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Cannot start service with" in combined
        assert str(blocker) in combined  # Server log path in fail-closed text
        assert "ROLLBACK FAILED" in captured.err
        assert "Traceback" not in captured.err
        assert repo.head() == previous_sha
        assert 'version = "1.0"' in repo.read("pyproject.toml")
    finally:
        if blocker.exists():
            blocker.unlink()


# --- Locking, stop command, guards ----------------------------------------


def test_stop_command_stops_running_service(repo, monkeypatch, capsys):
    module = _load_update_module_for(repo)
    old_pid = 4242
    _publish_pid(module, old_pid)
    state = _fake_service_lifecycle(module, monkeypatch, [old_pid])

    assert module.main(["--stop"]) == 0

    assert state["terminated"] == [old_pid]
    assert state["forced"] == []
    assert not repo.pid_file.exists()
    assert "WebAI-to-API stopped." in capsys.readouterr().out


def test_stale_pid_file_cleaned_by_stop_command(repo, capsys):
    module = _load_update_module_for(repo)
    _publish_pid(module, 999999999)

    assert module.main(["--stop"]) == 0

    assert "stale PID file" in capsys.readouterr().out
    assert not repo.pid_file.exists()


def test_normal_flow_leaves_no_stale_pid_file_when_stopped(repo):
    repo.remote_set_version("2.0")

    result = repo.run_updater()

    assert result.returncode == 0
    assert not repo.pid_file.exists()


LOCK_CONTENTION_TEXT = (
    "Another update operation is still in progress; "
    "requested action was not performed."
)


def _forbidden(step):
    def _fail(*_args, **_kwargs):
        pytest.fail(f"{step} must not run while updater lock is contended")
    return _fail


def _fresh_update_module(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_service_lifecycle(module, monkeypatch, live_pids=()):
    """Fake service-boundary stop for generic orchestration tests.

    Native POSIX/Windows stop mechanics are covered by dedicated
    platform/integration suites; these tests must not enter either platform
    stop budget.
    """
    state = {
        "alive": {pid: True for pid in live_pids},
        "terminated": [],
        "forced": [],
    }

    def fake_stop(pid):
        state["terminated"].append(pid)
        state["alive"][pid] = False
        try:
            with open(module.PID_FILE, encoding="utf-8") as handle:
                current = handle.read().strip()
            if current == str(pid):
                os.unlink(module.PID_FILE)
        except FileNotFoundError:
            pass

    monkeypatch.setattr(module, "stop_service", fake_stop)
    monkeypatch.setattr(
        module, "platform_pid_alive",
        lambda pid: state["alive"].get(pid, False),
    )
    return state


def _publish_pid(module, pid):
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(pid))


def _run_main_expecting_exit(module, argv=()):
    """Run main() for flows ending in die(); returns the exit code."""
    with pytest.raises(SystemExit) as excinfo:
        module.main(list(argv))
    return excinfo.value.code


def _load_fast_wait_module(repo):
    module = _load_update_module_for(repo)
    module.EXPLICIT_LOCK_WAIT_SECONDS = 0.05
    return module


@POSIX_ONLY
def test_updater_lock_blocks_second_instance(repo, monkeypatch, capsys):
    import fcntl
    fd = os.open(str(repo.lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        module = _load_fast_wait_module(repo)
        monkeypatch.setattr(module, "preflight", _forbidden("preflight"))
        head_before = repo.head()
        rc = module.main([])
        captured = capsys.readouterr()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert rc == 1
    assert LOCK_CONTENTION_TEXT in captured.err
    assert repo.head() == head_before
    assert not repo.pid_file.exists()


def test_lock_released_after_run(repo):
    assert repo.run_updater().returncode == 0
    assert repo.run_updater().returncode == 0


def test_explicit_lock_helper_retries_until_acquired(repo, monkeypatch):
    module = _load_update_module_for(repo)
    attempts = []
    handle = object()

    def fake_acquire(path):
        attempts.append(path)
        return handle if len(attempts) >= 3 else None

    monkeypatch.setattr(module, "platform_acquire_lock", fake_acquire)

    assert module.acquire_explicit_update_lock() is handle
    assert len(attempts) == 3


def test_explicit_lock_helper_returns_none_after_deadline(repo, monkeypatch):
    module = _load_fast_wait_module(repo)
    calls = []
    monkeypatch.setattr(
        module, "platform_acquire_lock",
        lambda path: calls.append(path) or None,
    )

    started = time.monotonic()
    assert module.acquire_explicit_update_lock() is None

    assert time.monotonic() - started >= 0.05
    assert len(calls) >= 2  # polled repeatedly instead of a single attempt


def test_explicit_wait_bound_covers_checker_cleanup_margin():
    from app.utils.update_check import CHECK_TIMEOUT_SECONDS

    import importlib.util
    spec = importlib.util.spec_from_file_location("update_bound", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.EXPLICIT_LOCK_POLL_SECONDS == 0.1
    # Current policy: checker deadline (10s) + ~1s subprocess termination
    # allowance + scheduler margin.
    assert module.EXPLICIT_LOCK_WAIT_SECONDS == 12.0
    # Drift guard: the explicit bound must always exceed the startup
    # checker window by at least its cleanup + scheduling allowance.
    assert module.EXPLICIT_LOCK_WAIT_SECONDS >= (
        CHECK_TIMEOUT_SECONDS + 2.0
    )


def test_stop_contention_times_out_without_stopping(repo, monkeypatch, capsys):
    module = _load_fast_wait_module(repo)
    monkeypatch.setattr(
        module, "platform_acquire_lock", lambda _path: None
    )  # simulated permanent contention
    with open(module.PID_FILE, "w") as handle:
        handle.write("999999999")
    monkeypatch.setattr(module, "stop_service", _forbidden("stop_service"))

    rc = module.main(["--stop"])
    captured = capsys.readouterr()

    assert rc == 1
    assert LOCK_CONTENTION_TEXT in captured.err
    with open(module.PID_FILE) as handle:
        assert handle.read().strip() == "999999999"


def test_update_contention_times_out_without_preflight(
    repo, monkeypatch, capsys,
):
    module = _load_fast_wait_module(repo)
    monkeypatch.setattr(
        module, "platform_acquire_lock", lambda _path: None
    )  # simulated permanent contention
    monkeypatch.setattr(module, "preflight", _forbidden("preflight"))
    head_before = repo.head()

    rc = module.main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert LOCK_CONTENTION_TEXT in captured.err
    assert repo.head() == head_before


@POSIX_ONLY
def test_stop_proceeds_after_transient_lock_release(repo, monkeypatch):
    """Real kernel-lock contention, deterministic handoff: a holder thread
    owns the flock before the stop flow starts; the worker's first
    non-blocking acquire observes contention (signalled), then the holder
    releases and the requested stop executes for real."""
    import fcntl
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from integration.updater._harness import spawn_service

    def track(process):
        repo._processes[process.pid] = process

    process = spawn_service(
        repo,
        [sys.executable, "-c", "import time; time.sleep(60)"],
        track,
        env=repo.env(),
    )
    old_pid = process.pid
    module = _load_update_module_for(repo)
    _publish_pid(module, old_pid)
    real_acquire = module.platform_acquire_lock
    contended = threading.Event()
    holder_ready = threading.Event()
    release = threading.Event()

    def counting_acquire(path):
        handle = real_acquire(path)
        if handle is None:
            contended.set()  # first poll observed the held kernel lock
        return handle

    monkeypatch.setattr(module, "platform_acquire_lock", counting_acquire)

    fd = os.open(str(repo.lock_file), os.O_CREAT | os.O_RDWR, 0o644)

    def holder():
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            holder_ready.set()
            release.wait(timeout=30)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)

    holder_thread = threading.Thread(target=holder)
    outcome = {}
    stopped_cleanly = False

    def run():
        outcome["rc"] = module.main(["--stop"])

    worker = threading.Thread(target=run)
    try:
        holder_thread.start()
        assert holder_ready.wait(timeout=5), (
            "holder never acquired the kernel lock"
        )
        worker.start()
        assert contended.wait(timeout=10), (
            "stop flow never reached the lock-contention path"
        )
        release.set()
        worker.join(timeout=30)
        holder_thread.join(timeout=10)

        # Assert the requested stop actually happened BEFORE any defensive
        # cleanup, so a false-success stop cannot be masked.
        process = repo._processes[old_pid]
        assert outcome.get("rc") == 0
        process.wait(timeout=5)
        assert process.poll() is not None
        assert not repo.pid_file.exists()
        stopped_cleanly = True
    finally:
        release.set()  # unblocks holder on every path
        if holder_thread.is_alive():
            holder_thread.join(timeout=30)
        if worker.is_alive():
            worker.join(timeout=30)
        os.close(fd)
        if not stopped_cleanly:
            repo.cleanup_pids(old_pid)

    assert outcome.get("rc") == 0


def test_update_proceeds_after_transient_lock_release(repo, monkeypatch):
    """First acquire attempt returns None (contention observed), later poll
    returns the real handle once the simulated holder exits, and the update
    flow then completes."""
    module = _load_update_module_for(repo)
    real_acquire = module.platform_acquire_lock
    contended = threading.Event()
    release = threading.Event()
    attempts = []

    def transient_acquire(path):
        attempts.append(path)
        if len(attempts) == 1:
            contended.set()
            return None
        return real_acquire(path) if release.is_set() else None

    monkeypatch.setattr(module, "platform_acquire_lock", transient_acquire)

    outcome = {}

    def run():
        outcome["rc"] = module.main([])

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert contended.wait(timeout=10), "no contended attempt observed"
        release.set()  # simulated background-check holder exits
        worker.join(timeout=30)
    finally:
        release.set()
        worker.join(timeout=30)

    assert outcome.get("rc") == 0
    assert len(attempts) >= 2


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


def test_missing_poetry_executable_rolls_back_with_clean_error(
    repo, monkeypatch, capsys,
):
    module = _load_update_module_for(repo)
    module.POETRY_COMMAND = "/nonexistent/poetry-missing"
    previous_sha = repo.head()
    lock_before = repo.read("poetry.lock")
    repo.remote_set_version("2.0", playwright="^9.9.9")

    code = _run_main_expecting_exit(module)

    assert code == 1
    assert "Cannot execute '/nonexistent/poetry-missing'" in capsys.readouterr().err
    assert repo.head() == previous_sha
    assert repo.read("poetry.lock") == lock_before


def _run_fail_closed_start_test(repo, monkeypatch, start_command):
    module = _load_update_module_for(repo)
    module.START_COMMAND = start_command
    old_pid = 4242
    _publish_pid(module, old_pid)
    state = _fake_service_lifecycle(module, monkeypatch, [old_pid])
    previous_sha = repo.head()
    repo.remote_set_version("2.0")
    return module, state, old_pid, previous_sha


def test_malformed_start_command_is_fail_closed_rollback(
    repo, monkeypatch, capsys,
):
    module, state, old_pid, previous_sha = _run_fail_closed_start_test(
        repo, monkeypatch, 'poetry run "unclosed quote'
    )

    code = _run_main_expecting_exit(module)

    assert code == 1
    captured = capsys.readouterr()
    assert "ROLLBACK FAILED" in captured.err
    assert "left STOPPED" in captured.err
    assert repo.head() == previous_sha
    assert "Traceback" not in captured.err
    assert not repo.pid_file.exists()
    assert state["terminated"] == [old_pid]


def test_empty_start_command_is_fail_closed_rollback(
    repo, monkeypatch, capsys,
):
    module, state, old_pid, previous_sha = _run_fail_closed_start_test(
        repo, monkeypatch, ""
    )

    code = _run_main_expecting_exit(module)

    assert code == 1
    captured = capsys.readouterr()
    assert "ROLLBACK FAILED" in captured.err
    assert "left STOPPED" in captured.err
    assert "START_COMMAND is empty" in captured.err
    assert repo.head() == previous_sha
    assert not repo.pid_file.exists()
    assert state["terminated"] == [old_pid]


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


@POSIX_ONLY
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


@POSIX_ONLY
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


def _load_windows_adoption_module(repo):
    module = _load_update_module_for(repo)
    module.IS_WINDOWS = True
    module.SHUTDOWN_CONTROL_FILE = str(repo.base / "shutdown-control.json")
    return module


def _write_windows_metadata(module, pid):
    with open(module.SHUTDOWN_CONTROL_FILE, "w", encoding="utf-8") as handle:
        json.dump({"port": 12345, "token": "listener-token", "pid": pid}, handle)


@pytest.mark.parametrize("launcher_alive", [True, False])
def test_windows_existing_service_reconciles_authoritative_pid(
    repo, monkeypatch, launcher_alive
):
    module = _load_windows_adoption_module(repo)
    launcher_pid = 101
    server_pid = 202
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(launcher_pid))
    _write_windows_metadata(module, server_pid)

    monkeypatch.setattr(
        module,
        "platform_pid_alive",
        lambda pid: launcher_alive if pid == launcher_pid else pid == server_pid,
    )
    monkeypatch.setattr(
        module,
        "_identify_windows_server",
        lambda *_args, **_kwargs: server_pid,
    )

    assert module.running_service_pid() == server_pid
    assert _pid_from(repo.pid_file) == server_pid


def test_windows_existing_service_same_pid_is_noop(repo, monkeypatch):
    module = _load_windows_adoption_module(repo)
    pid = 202
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(pid))
    _write_windows_metadata(module, pid)
    liveness_calls = []
    monkeypatch.setattr(
        module,
        "platform_pid_alive",
        lambda candidate: (liveness_calls.append(candidate) or True),
    )
    monkeypatch.setattr(
        module,
        "_write_pid_file",
        lambda _pid: pytest.fail("same PID must not be rewritten"),
    )
    monkeypatch.setattr(
        module,
        "_identify_windows_server",
        lambda *_args, **_kwargs: pytest.fail(
            "same PID must not require identity"
        ),
    )

    assert module.running_service_pid() == pid
    assert _pid_from(repo.pid_file) == pid
    assert liveness_calls == [pid, pid]


def test_windows_existing_service_malformed_metadata_preserves_pid(
    repo, monkeypatch
):
    module = _load_windows_adoption_module(repo)
    launcher_pid = 101
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(launcher_pid))
    with open(module.SHUTDOWN_CONTROL_FILE, "wb") as handle:
        handle.write(b"not-json")
    monkeypatch.setattr(module, "platform_pid_alive", lambda _pid: True)

    assert module.running_service_pid() == launcher_pid
    assert _pid_from(repo.pid_file) == launcher_pid


def test_windows_existing_service_legacy_metadata_preserves_pid(
    repo, monkeypatch
):
    module = _load_windows_adoption_module(repo)
    launcher_pid = 101
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(launcher_pid))
    with open(module.SHUTDOWN_CONTROL_FILE, "w", encoding="utf-8") as handle:
        json.dump({"port": 12345, "token": "legacy-token"}, handle)
    monkeypatch.setattr(module, "platform_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        module,
        "_identify_windows_server",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy metadata must not require identity"
        ),
    )

    assert module.running_service_pid() == launcher_pid
    assert _pid_from(repo.pid_file) == launcher_pid


def test_windows_existing_service_dead_metadata_pid_is_not_adopted(
    repo, monkeypatch
):
    module = _load_windows_adoption_module(repo)
    launcher_pid = 101
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(launcher_pid))
    _write_windows_metadata(module, 202)
    monkeypatch.setattr(
        module, "platform_pid_alive", lambda pid: pid == launcher_pid
    )

    assert module.running_service_pid() == launcher_pid
    assert _pid_from(repo.pid_file) == launcher_pid


@pytest.mark.parametrize(
    "launcher_alive, identity_pid, expected",
    [
        (True, None, 101),
        (False, None, None),
        (True, 303, 101),
        (False, 303, None),
    ],
)
def test_windows_existing_service_unproven_identity_is_not_adopted(
    repo, monkeypatch, launcher_alive, identity_pid, expected
):
    module = _load_windows_adoption_module(repo)
    launcher_pid = 101
    server_pid = 202
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(launcher_pid))
    _write_windows_metadata(module, server_pid)
    monkeypatch.setattr(
        module,
        "platform_pid_alive",
        lambda pid: launcher_alive if pid == launcher_pid else True,
    )
    monkeypatch.setattr(
        module,
        "_identify_windows_server",
        lambda *_args, **_kwargs: identity_pid,
    )

    assert module.running_service_pid() == expected
    assert _pid_from(repo.pid_file) == launcher_pid


def test_windows_existing_service_metadata_liveness_failure_is_clean(
    repo, monkeypatch
):
    module = _load_windows_adoption_module(repo)
    launcher_pid = 101
    server_pid = 202
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(launcher_pid))
    _write_windows_metadata(module, server_pid)

    def fake_alive(pid):
        if pid == launcher_pid:
            return True
        raise module.PlatformOperationError(OSError("probe failed"))

    monkeypatch.setattr(module, "platform_pid_alive", fake_alive)

    with pytest.raises(module.UpdateError, match="metadata PID 202 liveness"):
        module.running_service_pid()
    assert _pid_from(repo.pid_file) == launcher_pid


def test_windows_existing_service_pid_rewrite_failure_is_clean(
    repo, monkeypatch
):
    module = _load_windows_adoption_module(repo)
    launcher_pid = 101
    server_pid = 202
    with open(module.PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(launcher_pid))
    _write_windows_metadata(module, server_pid)
    monkeypatch.setattr(module, "platform_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        module,
        "_identify_windows_server",
        lambda *_args, **_kwargs: server_pid,
    )

    def fail_write(_pid):
        raise OSError("disk full")

    monkeypatch.setattr(module, "_write_pid_file", fail_write)

    with pytest.raises(module.UpdateError, match="reconcile service PID file"):
        module.running_service_pid()
    assert _pid_from(repo.pid_file) == launcher_pid


@pytest.mark.parametrize(
    "metadata",
    [
        {"port": 1, "token": "t"},
        {"port": 1, "token": "t", "pid": True},
        {"port": 1, "token": "t", "pid": 0},
        {"port": 1, "token": "t", "pid": -1},
        {"port": 1, "token": "t", "pid": "42"},
        b"not-json",
    ],
)
def test_windows_adoption_rejects_invalid_server_pid_metadata(repo, metadata):
    module = _load_windows_adoption_module(repo)
    raw = metadata if isinstance(metadata, bytes) else json.dumps(metadata).encode()

    assert module._parse_shutdown_metadata(raw) is None


def test_windows_adoption_rejects_stale_metadata(repo):
    module = _load_windows_adoption_module(repo)
    stale = {"port": 1, "token": "old", "pid": 4242}
    raw = json.dumps(stale).encode()
    with open(module.SHUTDOWN_CONTROL_FILE, "wb") as handle:
        handle.write(raw)
    module.WINDOWS_METADATA_WAIT_SECONDS = 0

    with pytest.raises(module.UpdateError, match="fresh live server PID"):
        module._adopt_windows_server_pid(1111, raw)


@pytest.mark.parametrize("server_pid", [101, 202])
def test_windows_start_adopts_fresh_live_server_pid(
    repo, monkeypatch, server_pid
):
    module = _load_windows_adoption_module(repo)
    launcher_pid = 101
    stale = {"port": 1, "token": "old", "pid": 404}
    fresh = {"port": 2, "token": "new", "pid": server_pid}
    with open(module.SHUTDOWN_CONTROL_FILE, "w", encoding="utf-8") as handle:
        json.dump(stale, handle)

    class FakeProcess:
        pid = launcher_pid

        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return None

    process = FakeProcess()

    def fake_spawn(_argv, _cwd, log_handle):
        log_handle.close()
        with open(module.SHUTDOWN_CONTROL_FILE, "w", encoding="utf-8") as handle:
            json.dump(fresh, handle)
        return process

    monkeypatch.setattr(module, "parse_start_command", lambda: ["launcher"])
    monkeypatch.setattr(module, "platform_spawn_detached", fake_spawn)
    monkeypatch.setattr(module, "platform_pid_alive", lambda pid: True)
    identity_calls = []
    monkeypatch.setattr(
        module,
        "_identify_windows_server",
        lambda *_args, **_kwargs: (
            identity_calls.append(True) or fresh["pid"]
        ),
    )

    started = module.start_service()

    assert started is process
    assert _pid_from(repo.pid_file) == server_pid
    assert not process.killed
    assert identity_calls == ([] if server_pid == launcher_pid else [True])


@pytest.mark.parametrize("identity_pid", [None, 303])
def test_windows_start_rejects_unproven_different_pid(
    repo, monkeypatch, identity_pid
):
    module = _load_windows_adoption_module(repo)
    module.WINDOWS_METADATA_WAIT_SECONDS = 1
    module.WINDOWS_METADATA_POLL_SECONDS = 0.1
    clock = _FakeClock()
    monkeypatch.setattr(module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)

    launcher_pid = 101
    server_pid = 202
    stale = {"port": 1, "token": "old", "pid": 404}
    fresh = {"port": 2, "token": "new", "pid": server_pid}
    with open(module.SHUTDOWN_CONTROL_FILE, "w", encoding="utf-8") as handle:
        json.dump(stale, handle)

    class FakeProcess:
        pid = launcher_pid

        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return None

    process = FakeProcess()
    force_calls = []

    def fake_spawn(_argv, _cwd, log_handle):
        log_handle.close()
        with open(module.SHUTDOWN_CONTROL_FILE, "w", encoding="utf-8") as handle:
            json.dump(fresh, handle)
        return process

    monkeypatch.setattr(module, "parse_start_command", lambda: ["launcher"])
    monkeypatch.setattr(module, "platform_spawn_detached", fake_spawn)
    monkeypatch.setattr(module, "platform_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        module,
        "_identify_windows_server",
        lambda *_args, **_kwargs: identity_pid,
    )
    monkeypatch.setattr(
        module,
        "platform_force_kill",
        lambda pid: force_calls.append(pid),
    )

    with pytest.raises(module.UpdateError, match="identity"):
        module.start_service()

    assert process.killed
    assert force_calls == []
    assert not os.path.exists(module.PID_FILE)


def test_windows_adoption_requires_live_server_pid(repo):
    module = _load_windows_adoption_module(repo)
    raw = json.dumps({"port": 1, "token": "new", "pid": 202}).encode()
    with open(module.SHUTDOWN_CONTROL_FILE, "wb") as handle:
        handle.write(raw)
    module.WINDOWS_METADATA_WAIT_SECONDS = 0

    with pytest.raises(module.UpdateError, match="fresh live server PID"):
        module._adopt_windows_server_pid(101, b"old")


def test_windows_adoption_failure_cleans_launcher(repo, monkeypatch):
    module = _load_windows_adoption_module(repo)
    module.WINDOWS_METADATA_WAIT_SECONDS = 0

    class FakeProcess:
        pid = 101

        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return None

    process = FakeProcess()
    monkeypatch.setattr(module, "parse_start_command", lambda: ["launcher"])
    monkeypatch.setattr(
        module,
        "platform_spawn_detached",
        lambda _argv, _cwd, log_handle: (log_handle.close(), process)[1],
    )

    with pytest.raises(module.UpdateError, match="fresh live server PID"):
        module.start_service()

    assert process.killed
    assert not os.path.exists(module.PID_FILE)


def test_pid_file_publish_is_atomic(repo):
    module = _load_windows_adoption_module(repo)

    module._write_pid_file(202)

    assert _pid_from(repo.pid_file) == 202
    assert not list(repo.base.glob(f".{repo.pid_file.name}.*"))


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
    module = _load_update_module_for(repo, monkeypatch)
    old_pid = 4242
    _publish_pid(module, old_pid)
    state = _fake_service_lifecycle(module, monkeypatch, [old_pid])

    assert module.main(["--stop"]) == 0

    assert state["terminated"] == [old_pid]
    assert not repo.pid_file.exists()


@POSIX_ONLY
def test_docker_stop_blocked_by_active_updater_lock(repo, monkeypatch, capsys):
    import fcntl
    old_pid = repo.start_fake_service()
    fd = os.open(str(repo.lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        module = _load_fast_wait_module(repo)
        # Re-apply the Docker guard simulation used by the shared loader.
        real_exists = os.path.exists

        def fake_exists(path):
            if path == "/.dockerenv":
                return True
            return real_exists(path)

        monkeypatch.setattr(os.path, "exists", fake_exists)
        monkeypatch.setattr(module, "stop_service", _forbidden("stop_service"))
        rc = module.main(["--stop"])
        captured = capsys.readouterr()

        assert rc == 1
        assert LOCK_CONTENTION_TEXT in captured.err
        assert repo.pid_alive(old_pid)
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


@POSIX_ONLY
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


@POSIX_ONLY
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


def _windows_stop_module(
    repo, monkeypatch, ipc_results, alive_sequence, force_alive_sequence=None
):
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
        if isinstance(state, Exception):
            raise state
        alive_calls.append(state)
        return state

    force_calls = []
    monkeypatch.setattr(module, "platform_pid_alive", fake_alive)

    def fake_force(pid):
        force_calls.append(pid)
        if force_alive_sequence is not None:
            alive_sequence[:] = list(force_alive_sequence)

    monkeypatch.setattr(
        module, "platform_force_kill", fake_force,
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
                force_alive_sequence=[False],
            )
        )

        module.stop_service(4321)

        assert len(ipc_calls) == 10       # one attempt per grace tick
        assert force_calls == [4321]      # fallback engaged exactly once
        elapsed = clock.now - clock.start
        assert elapsed <= (
            module.WINDOWS_STOP_BUDGET_SECONDS
            + module.WINDOWS_FORCE_CONFIRM_TIMEOUT_SECONDS
            + 1e-9
        )
        assert all(t is not None and t <= 3.0 for t in timeouts)
        assert timeouts[-1] < timeouts[0]  # cap shrinks near deadline
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_force_kill_waits_for_delayed_death(repo, monkeypatch):
    old_pid = repo.start_fake_service()
    try:
        module, _ipc_calls, force_calls, _timeouts, clock = (
            _windows_stop_module(
                repo,
                monkeypatch,
                ipc_results=[],
                alive_sequence=[True],
                force_alive_sequence=[True, True, False],
            )
        )

        module.stop_service(4321)

        assert force_calls == [4321]
        assert clock.now > module.WINDOWS_STOP_BUDGET_SECONDS
        assert not repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_force_kill_timeout_preserves_state(repo, monkeypatch, capsys):
    old_pid = repo.start_fake_service()
    try:
        module, _ipc_calls, force_calls, _timeouts, _clock = (
            _windows_stop_module(
                repo,
                monkeypatch,
                ipc_results=[],
                alive_sequence=[True],
                force_alive_sequence=[True],
            )
        )

        assert module.main(["--stop"]) == 1
        captured = capsys.readouterr()

        assert force_calls == [old_pid]
        assert "remained alive after force termination" in captured.err
        assert "WebAI-to-API stopped." not in captured.out
        assert repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_force_liveness_error_preserves_state(repo, monkeypatch, capsys):
    old_pid = repo.start_fake_service()
    try:
        liveness_error = PlatformOperationError(
            OSError("liveness query failed")
        )
        module, _ipc_calls, force_calls, _timeouts, _clock = (
            _windows_stop_module(
                repo,
                monkeypatch,
                ipc_results=[],
                alive_sequence=[True],
                force_alive_sequence=[liveness_error],
            )
        )

        assert module.main(["--stop"]) == 1
        captured = capsys.readouterr()

        assert force_calls == [old_pid]
        assert "Cannot confirm force-killed service PID" in captured.err
        assert repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_windows_stop_initial_liveness_error_is_clean(repo, monkeypatch, capsys):
    old_pid = repo.start_fake_service()
    try:
        liveness_error = PlatformOperationError(
            OSError("liveness query failed")
        )
        module, _ipc_calls, force_calls, _timeouts, _clock = (
            _windows_stop_module(
                repo,
                monkeypatch,
                ipc_results=[],
                alive_sequence=[liveness_error],
            )
        )

        assert module.main(["--stop"]) == 1
        captured = capsys.readouterr()

        assert force_calls == []
        assert "Cannot query service PID" in captured.err
        assert "WebAI-to-API stopped." not in captured.out
        assert repo.pid_file.exists()
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
                    force_alive_sequence=[False],
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

    # Host defaults preserve /tmp on POSIX and use the system temp directory
    # on Windows.
    host_base = "/tmp" if os.name == "posix" else tempfile.gettempdir()
    assert module.PID_FILE == os.path.join(host_base, "webai-to-api.pid")
    assert module.LOCK_FILE == os.path.join(
        host_base, "webai-to-api-update.lock"
    )

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


@POSIX_ONLY
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

    class FakePopen:
        def __init__(self, pid):
            self.pid = pid

        def kill(self):
            pass

    try:
        module, _ipc_calls, _force_calls, _timeouts, _clock = (
            _windows_stop_module(
                repo, monkeypatch,
                ipc_results=[],
                alive_sequence=[True, False, True, False],
            )
        )
        module.START_COMMAND = (
            '"C:\\Python\\python.exe" -c "import time; time.sleep(30)"'
        )
        module.HEALTH_TIMEOUT = 1
        module.HEALTH_INTERVAL = 0.1
        module.SHUTDOWN_CONTROL_FILE = str(repo.base / "shutdown-control.json")
        monkeypatch.setattr(
            module,
            "_adopt_windows_server_pid",
            lambda launcher_pid, _previous_raw: launcher_pid,
        )
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
        monkeypatch.setattr(
            module.shutil, "which", lambda _name: "C:\\Resolved\\python.exe"
        )
        repo.remote_set_version("2.0")

        with pytest.raises(SystemExit):
            module.main([])  # health gate will fail -> rollback path exits

        assert spawned["cwd"] == module.ROOT
        assert spawned["argv"][0] == "C:\\Resolved\\python.exe"
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
