"""
End-to-end tests for scripts/update.py (Git-based updater).

Each test builds a temporary origin (bare repo) plus two clones:
- `work`:   the installation the updater runs against; intentionally stays
            behind so its VERSION differs from origin/master.
- `editor`: simulates upstream developers advancing origin/master.

The updater runs via subprocess with injected environment: PID/log/lock
paths, start command, health endpoint, and a fake `poetry` on PATH that
records invocations.
"""

import http.server
import os
import socket
import subprocess
import sys
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

        # c1: app files only, NO VERSION (simulates pre-VERSION installs).
        self._files_on_disk(self.work, {
            "app.txt": "one\n",
            "pyproject.toml": "[project]\nname='webai'\n",
            "poetry.lock": "# lock v1\n",
        })
        self.commit("initial")
        # c2: upstream publishes VERSION 1.0; work fast-forwards onto it.
        git(self.editor, "pull", "origin", "master")
        self._files_on_disk(self.editor, {"VERSION": "1.0\n"})
        self.commit("add version", target="editor")
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
        # force_add lets the simulated upstream track paths the local
        # .gitignore would otherwise skip (e.g., logs/, .env).
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


def test_same_version_is_noop(repo):
    result = repo.run_updater()
    assert result.returncode == 0
    assert "Already up to date" in result.stdout


def test_different_version_moves_head_to_origin_master(repo):
    repo.remote_bump({"VERSION": "2.0\n"})
    expected_head = repo.editor_head()

    result = repo.run_updater()

    assert result.returncode == 0
    assert repo.head() == expected_head
    assert repo.read("VERSION") == "2.0\n"


def test_missing_local_version_triggers_update(repo):
    # Rewind work to c1 (pre-VERSION history); c1 is an ancestor of origin.
    first_commit = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=repo.work, check=True, capture_output=True, text=True,
    ).stdout.splitlines()[0]
    git(repo.work, "reset", "--hard", first_commit)
    repo.remote_bump({"VERSION": "2.0\n"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert repo.read("VERSION") == "2.0\n"


def test_missing_remote_version_aborts_untouched(repo):
    os.unlink(os.path.join(repo.editor, "VERSION"))
    repo.commit("remove version", target="editor")
    local_before = repo.head()

    result = repo.run_updater()

    assert result.returncode != 0
    assert "No readable VERSION" in result.stderr
    assert repo.head() == local_before


def test_fetch_failure_aborts_without_touching_worktree(repo):
    git(repo.work, "remote", "set-url", "origin", "/nonexistent/remote.git")
    head_before = repo.head()

    result = repo.run_updater()

    assert result.returncode != 0
    assert "git fetch failed" in result.stderr
    assert repo.head() == head_before


def test_dirty_tracked_file_aborts_and_preserves_file(repo):
    repo.remote_bump({"VERSION": "2.0\n"})
    version_path = os.path.join(repo.work, "VERSION")
    with open(version_path, "w") as handle:
        handle.write("local-edit\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "tracked/staged modifications" in result.stderr
    assert open(version_path).read() == "local-edit\n"


def test_staged_change_aborts(repo):
    repo.remote_bump({"VERSION": "2.0\n"})
    repo._files_on_disk(repo.work, {"app.txt": "staged-change\n"})
    git(repo.work, "add", "-A")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "tracked/staged modifications" in result.stderr


def test_local_commit_ahead_aborts(repo):
    repo._files_on_disk(repo.work, {"VERSION": "1.0-local\n"})
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


def test_detached_head_aborts(repo):
    sha = repo.head()
    git(repo.work, "checkout", "--detach", sha)

    result = repo.run_updater()

    assert result.returncode != 0
    assert "detached HEAD" in result.stderr


def test_protected_remote_path_aborts(repo):
    repo.remote_bump({".env": "EVIL=1\n", "VERSION": "2.0\n"})

    result = repo.run_updater()

    assert result.returncode != 0
    assert "protected user-owned paths" in result.stderr


def test_untracked_collision_aborts_and_preserves_file(repo):
    repo.remote_bump({"newfile.txt": "remote\n", "VERSION": "2.0\n"})
    collision = os.path.join(repo.work, "newfile.txt")
    with open(collision, "w") as handle:
        handle.write("precious local data\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "untracked/ignored local files" in result.stderr
    assert open(collision).read() == "precious local data\n"


def test_dependency_sync_runs_only_when_dep_files_change(repo):
    repo.remote_bump({"VERSION": "2.0\n"})          # code-only change
    assert repo.run_updater().returncode == 0
    assert repo.poetry_calls() == []

    repo.remote_bump({"poetry.lock": "# changed\n", "VERSION": "2.1\n"})  # lock change
    assert repo.run_updater().returncode == 0
    assert any("install --sync" in call for call in repo.poetry_calls())


def test_playwright_install_only_when_lock_changed(repo):
    repo.remote_bump({"pyproject.toml": "[project]\nname='x2'\n", "VERSION": "2.1\n"})
    assert repo.run_updater().returncode == 0
    assert not any("playwright" in call for call in repo.poetry_calls())

    repo.remote_bump({"poetry.lock": "# changed again\n", "VERSION": "2.2\n"})
    assert repo.run_updater().returncode == 0
    assert any(
        "run playwright install chromium" in call for call in repo.poetry_calls()
    )


def test_running_service_is_stopped_updated_restarted(repo):
    health = FlakyHealth(fail_first=0)
    old_pid = repo.start_fake_service()
    try:
        repo.remote_bump({"VERSION": "2.0\n"})
        result = repo.run_updater(extra_env={
            "WEBAI_HEALTH_URL": health.url,
            "WEBAI_HEALTH_TIMEOUT": "5",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode == 0
        assert old_pid != _pid_from(repo.pid_file)
        new_pid = _pid_from(repo.pid_file)
        assert subprocess.run(["kill", "-0", str(new_pid)],
                              capture_output=True).returncode == 0
    finally:
        health.stop()
        repo.cleanup_pids(old_pid)


def test_previously_stopped_service_remains_stopped(repo):
    repo.remote_bump({"VERSION": "2.0\n"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert not repo.pid_file.exists()


def test_health_failure_restores_previous_sha(repo):
    # Endpoint unreachable: update-phase poll fails, rollback restores the
    # previous commit, and the rollback's own health check also fails -> the
    # loud ROLLBACK FAILED branch. Worktree must be back at the previous SHA.
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    dead_port = _free_port()
    try:
        repo.remote_bump({"VERSION": "2.0\n"})

        result = repo.run_updater(extra_env={
            "WEBAI_HEALTH_URL": f"http://127.0.0.1:{dead_port}/health",
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        assert "rolling back" in result.stderr.lower()
        assert repo.head() == previous_sha
        assert repo.read("VERSION") == "1.0\n"
    finally:
        repo.cleanup_pids(old_pid)


def test_dependency_failure_restores_previous_sha(repo):
    previous_sha = repo.head()
    lock_before = repo.read("poetry.lock")
    repo.remote_bump({
        "VERSION": "2.0\n",
        "poetry.lock": "# broken future lock\n",
    })

    result = repo.run_updater(extra_env={"FAKE_POETRY_EXIT": "3"})

    assert result.returncode != 0
    assert repo.head() == previous_sha
    assert repo.read("poetry.lock") == lock_before
    # Initial sync attempt + rollback re-sync against the restored lock.
    assert len([c for c in repo.poetry_calls() if "install --sync" in c]) >= 2


def test_updater_lock_blocks_second_instance(repo):
    import fcntl
    repo.lock_file.parent.mkdir(exist_ok=True)
    fd = os.open(repo.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # hold like a live updater

    try:
        result = repo.run_updater()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert result.returncode == 0
    assert "Another updater instance" in result.stderr
    # The active holder's lock file must not have been removed/recreated.
    assert repo.lock_file.exists()


def test_lock_released_after_run(repo):
    assert repo.run_updater().returncode == 0
    # A follow-up run must acquire the lock cleanly (released exactly once).
    assert repo.run_updater().returncode == 0


def test_stop_waits_for_active_updater_lock(repo):
    import fcntl
    old_pid = repo.start_fake_service()
    try:
        repo.lock_file.parent.mkdir(exist_ok=True)
        fd = os.open(repo.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # active updater

        result = subprocess.run(
            [sys.executable, UPDATE_PY, "--stop"],
            capture_output=True, text=True, timeout=60,
            env=repo.env(),
        )
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

        assert result.returncode == 0
        assert "Another updater instance" in result.stderr
        assert "Traceback" not in result.stderr
        # Service untouched: still alive, PID file intact.
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode == 0
        assert repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_malformed_start_command_rolls_back_fail_closed(repo):
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    try:
        repo.remote_bump({"VERSION": "2.0\n"})
        result = repo.run_updater(extra_env={
            'WEBAI_START_COMMAND': 'poetry run "unclosed quote',
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        assert "ROLLBACK FAILED" in result.stderr
        assert "left STOPPED" in result.stderr
        assert "Malformed START_COMMAND" in result.stdout + result.stderr or \
               "could not be restarted" in (result.stdout + result.stderr)
        assert repo.head() == previous_sha
        assert "Traceback" not in result.stderr
        assert not repo.pid_file.exists()
    finally:
        repo.cleanup_pids(old_pid)


def test_empty_start_command_rolls_back_fail_closed(repo):
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    try:
        repo.remote_bump({"VERSION": "2.0\n"})
        result = repo.run_updater(extra_env={
            "WEBAI_START_COMMAND": "",
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        assert "ROLLBACK FAILED" in result.stderr
        assert "left STOPPED" in result.stderr
        assert repo.head() == previous_sha
        assert "Traceback" not in result.stderr
    finally:
        repo.cleanup_pids(old_pid)


def test_lock_file_open_failure_is_clean_error(repo):
    blocker = os.path.join(repo.base, "lock-blocker")
    with open(blocker, "w") as handle:
        handle.write("x")
    # Lock path lives *under* a regular file -> open() fails.

    result = repo.run_updater(extra_env={
        "WEBAI_LOCK_FILE": os.path.join(blocker, "child.lock"),
    })

    assert result.returncode != 0
    assert "Cannot open updater lock file" in result.stderr
    assert "Traceback" not in result.stderr


def test_unexpected_flock_error_is_classified_as_error(monkeypatch):
    import errno
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_mod3", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def boom(fd, operation):
        raise OSError(errno.EIO, "Simulated I/O error")

    monkeypatch.setattr(module.fcntl, "flock", boom)
    with pytest.raises(module.UpdateError, match="Cannot lock updater lock file"):
        assert module.acquire_lock() is True or True


def test_symlink_ancestor_collision_aborts(repo):
    # Local symlink `foo` -> somewhere else; remote tracks `foo/bar.py`.
    repo.remote_bump({"foo/bar.py": "remote\n", "VERSION": "2.0\n"})
    link = os.path.join(repo.work, "foo")
    target = os.path.join(repo.base, "outside-target.txt")
    with open(target, "w") as handle:
        handle.write("outside\n")
    os.symlink(target, link)

    result = repo.run_updater()

    assert result.returncode != 0
    assert "untracked/ignored local files" in result.stderr
    # Symlink untouched; never written through.
    assert os.path.islink(link)
    assert open(target).read() == "outside\n"


def test_tracked_symlink_in_head_remains_allowed(repo):
    # Upstream ships a tracked symlink; work adopts it via pull, so HEAD
    # legitimately manages it and a later unrelated bump must succeed.
    git(repo.editor, "pull", "origin", "master")
    link = os.path.join(repo.editor, "shim")
    os.symlink("app.txt", link)
    repo.commit("add tracked symlink", target="editor")
    git(repo.work, "pull", "origin", "master")
    repo.remote_bump({"VERSION": "2.0\n"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert os.path.islink(os.path.join(repo.work, "shim"))


def _load_update_module_for(repo, monkeypatch):
    """Load update.py with paths bound to the test repo and Docker flagged."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_docker", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env = repo.env()
    module.ROOT = env["WEBAI_ROOT"]
    module.PID_FILE = env["WEBAI_PID_FILE"]
    module.LOG_FILE = env["WEBAI_LOG_FILE"]
    module.LOCK_FILE = env["WEBAI_LOCK_FILE"]

    real_exists = os.path.exists

    def fake_exists(path):
        if path == "/.dockerenv":
            return True
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", fake_exists)
    return module


def test_docker_normal_update_is_refused(repo, monkeypatch, capsys):
    repo.remote_bump({"VERSION": "2.0\n"})
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


def test_docker_stop_with_stale_pid_cleans_up(repo, monkeypatch):
    repo.pid_file.write_text("999999999")
    module = _load_update_module_for(repo, monkeypatch)

    rc = module.main(["--stop"])

    assert rc == 0
    assert not repo.pid_file.exists()


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
        assert "Another updater instance" in captured.err
        # Service untouched.
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode == 0
        assert repo.pid_file.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        repo.cleanup_pids(old_pid)


def test_container_guard_refuses():
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_mod", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(module.UpdateError, match="docker compose up -d --build"):
        module.container_guard(exists=lambda p: p == "/.dockerenv")
    module.container_guard(exists=lambda p: False)


def test_example_files_are_not_protected(repo):
    repo.remote_bump({
        ".env.example": "ATLASCLOUD_API_KEY=\n",
        "config.conf.example": "[Gemini]\n",
        "VERSION": "2.0\n",
    })

    result = repo.run_updater()

    assert result.returncode == 0
    assert repo.head() == repo.editor_head()


def test_runtime_subtree_is_protected_but_examples_allowed(repo):
    assert repo.run_updater is not None
    import importlib.util
    spec = importlib.util.spec_from_file_location("update_mod2", UPDATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert not module._is_protected_path(".env.example")
    assert not module._is_protected_path("config.conf.example")
    assert module._is_protected_path(".env")
    assert module._is_protected_path(".env.local")
    assert module._is_protected_path("config.conf")
    assert module._is_protected_path("runtime")
    assert module._is_protected_path("runtime/foo.db")


def test_nested_untracked_collision_aborts_and_preserves(repo):
    repo.remote_bump({"pkg/mod.py": "remote content\n", "VERSION": "2.0\n"})
    local = os.path.join(repo.work, "pkg", "mod.py")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    with open(local, "w") as handle:
        handle.write("precious\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "untracked/ignored local files" in result.stderr
    assert open(local).read() == "precious\n"


def test_ignored_file_collision_aborts_and_preserves(repo):
    repo.write_files({".gitignore": "logs/\n"})
    repo.commit("ignore logs", push=True)
    repo.remote_bump({"logs/app.log": "remote log\n", "VERSION": "2.0\n"},
                     force_add=True)
    ignored = os.path.join(repo.work, "logs", "app.log")
    os.makedirs(os.path.dirname(ignored), exist_ok=True)
    with open(ignored, "w") as handle:
        handle.write("precious local log\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "untracked/ignored local files" in result.stderr
    assert open(ignored).read() == "precious local log\n"


def test_non_colliding_ignored_files_remain_allowed(repo):
    repo.write_files({".gitignore": "logs/\n"})
    repo.commit("ignore logs", push=True)
    logs_dir = os.path.join(repo.work, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    with open(os.path.join(logs_dir, "other.log"), "w") as handle:
        handle.write("kept\n")
    repo.remote_bump({"VERSION": "2.0\n"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert open(os.path.join(logs_dir, "other.log")).read() == "kept\n"


def test_rollback_dep_restoration_failure_leaves_service_stopped(repo):
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    try:
        repo.remote_bump({"poetry.lock": "# broken future lock\n", "VERSION": "2.0\n"})

        result = repo.run_updater(extra_env={"FAKE_POETRY_EXIT": "3"})

        assert result.returncode != 0
        assert "ROLLBACK FAILED" in result.stderr
        assert "left STOPPED" in result.stderr
        assert repo.head() == previous_sha
        # Fail closed: no service restart after failed restoration.
        assert not repo.pid_file.exists()
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode != 0
    finally:
        repo.cleanup_pids(old_pid)


def test_invalid_start_command_rollback_restart_failure_is_fail_closed(repo):
    """
    Rollback restart failure: restoration succeeds, then starting the
    previous service raises -> ROLLBACK FAILED, service left STOPPED,
    previous SHA restored, no traceback, no stale PID file.
    """
    old_pid = repo.start_fake_service()
    previous_sha = repo.head()
    try:
        repo.remote_bump({"VERSION": "2.0\n"})
        result = repo.run_updater(extra_env={
            "WEBAI_START_COMMAND": "/nonexistent/start-binary",
            "WEBAI_HEALTH_TIMEOUT": "1",
            "WEBAI_HEALTH_INTERVAL": "0.1",
        })

        assert result.returncode != 0
        assert "ROLLBACK FAILED" in result.stderr
        assert "left STOPPED" in result.stderr
        assert repo.head() == previous_sha
        assert "Traceback" not in result.stderr
        # PID file must not point at a failed/stale new process.
        assert not repo.pid_file.exists()
        assert subprocess.run(["kill", "-0", str(old_pid)],
                              capture_output=True).returncode != 0
    finally:
        repo.cleanup_pids(old_pid)


def test_stale_pid_file_cleaned_by_stop_command(repo):
    repo.pid_file.write_text("999999999")  # nonexistent PID

    result = subprocess.run(
        [sys.executable, UPDATE_PY, "--stop"],
        capture_output=True, text=True, timeout=60,
        env=repo.env(),
    )

    assert result.returncode == 0
    assert "stale PID file" in result.stdout
    assert not repo.pid_file.exists()


def test_normal_flow_leaves_no_stale_pid_file_when_stopped(repo):
    repo.remote_bump({"VERSION": "2.0\n"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert not repo.pid_file.exists()


def test_missing_poetry_binary_triggers_clean_rollback(repo):
    previous_sha = repo.head()
    lock_before = repo.read("poetry.lock")
    repo.remote_bump({"poetry.lock": "# changed\n", "VERSION": "2.0\n"})

    result = repo.run_updater(extra_env={
        "WEBAI_POETRY": "/nonexistent/poetry-missing",
    })

    assert result.returncode != 0
    assert "Cannot execute '/nonexistent/poetry-missing'" in result.stderr
    assert repo.head() == previous_sha
    assert repo.read("poetry.lock") == lock_before


def test_preflight_failures_have_no_traceback(repo):
    git(repo.work, "checkout", "-b", "feature")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("ERROR: ")


def test_untracked_file_blocking_remote_directory_aborts(repo):
    # Local untracked FILE `foo` vs remote tracked `foo/bar.py`:
    # git cannot create directory `foo/` while file `foo` exists.
    repo.remote_bump({"foo/bar.py": "remote\n", "VERSION": "2.0\n"})
    blocker = os.path.join(repo.work, "foo")
    with open(blocker, "w") as handle:
        handle.write("blocks dir creation\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert "untracked/ignored local files" in result.stderr
    assert open(blocker).read() == "blocks dir creation\n"


def test_ignored_dir_file_collision_aborts(repo):
    repo.write_files({".gitignore": "cache/\n"})
    repo.commit("ignore cache", push=True)
    repo.remote_bump({"cache/data/file.txt": "data\n", "VERSION": "2.0\n"},
                     force_add=True)
    ignored = os.path.join(repo.work, "cache", "data", "file.txt")
    os.makedirs(os.path.dirname(ignored), exist_ok=True)
    with open(ignored, "w") as handle:
        handle.write("precious\n")

    result = repo.run_updater()

    assert result.returncode != 0
    assert open(ignored).read() == "precious\n"


def test_plain_local_directory_allows_nested_remote_file(repo):
    os.makedirs(os.path.join(repo.work, "docs"), exist_ok=True)
    repo.remote_bump({"docs/guide.md": "guide\n", "VERSION": "2.0\n"})

    result = repo.run_updater()

    assert result.returncode == 0
    assert repo.read("docs/guide.md") == "guide\n"
