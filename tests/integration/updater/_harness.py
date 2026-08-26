"""Harness helpers (repo builder, process utils) for integration tests.

Builds isolated local Git fixtures (bare origin + work/editor clones) and
drives the REAL scripts/update.py through subprocess with WEBAI_* env
overrides. Services are real processes (stdlib stub or the actual server via
the project's default START_COMMAND shape). No Docker, no network beyond
loopback, no Gemini/browser.
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request


# tests/integration/updater/_harness.py -> four dirnames reach repo root.
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
UPDATE_PY = os.path.join(REPO_ROOT, "scripts", "update.py")
RUN_PY = os.path.join(REPO_ROOT, "src", "run.py")
SERVICE_STUB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_service_stub.py"
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@test",
}

PYPROJECT_TEMPLATE = """\
[project]
name = "webai-to-api"
version = "{version}"
description = "integration fixture"
requires-python = ">=3.11,<3.13"
"""


def git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
    )


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def health_status(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status
    except Exception:
        return None


def wait_health(url, timeout=30.0, interval=0.2, expect=200, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health_status(url) == expect:
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(interval)
    return False


def port_serving(port):
    return health_status(f"http://127.0.0.1:{port}/health") is not None


def wait_port_closed(port, timeout=5.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_serving(port):
            return True
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval, remaining))
    return not port_serving(port)


class IntegrationRepo:
    """Bare origin + work clone (the installation) + editor clone."""

    def __init__(self, base_dir):
        self.base = str(base_dir)
        self.origin = os.path.join(self.base, "origin.git")
        self.work = os.path.join(self.base, "work")
        self.editor = os.path.join(self.base, "editor")
        self.runtime_dir = os.path.join(self.base, "runtime")
        self.pid_file = os.path.join(self.base, "service.pid")
        self.log_file = os.path.join(self.base, "service.log")
        self.lock_file = os.path.join(self.base, "update.lock")
        self._build()

    def _build(self):
        subprocess.run(
            ["git", "init", "--bare", "-b", "master", self.origin],
            check=True, capture_output=True,
        )
        for clone in (self.work, self.editor):
            subprocess.run(
                ["git", "clone", self.origin, clone],
                check=True, capture_output=True,
                env={**os.environ, **GIT_ENV},
            )
            git(clone, "checkout", "-b", "master")
        self._write(self.work, {"app.txt": "one\n"})
        self.commit("initial")
        git(self.editor, "pull", "origin", "master")
        self._write(self.editor, {"pyproject.toml": self._pyproject("1.0")})
        self.commit("pyproject 1.0", target="editor")
        git(self.work, "pull", "origin", "master")

    @staticmethod
    def _pyproject(version):
        return PYPROJECT_TEMPLATE.format(version=version)

    @staticmethod
    def _write(root, files):
        for name, content in files.items():
            path = os.path.join(root, name)
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w") as handle:
                handle.write(content)

    def commit(self, message, target="work"):
        root = self.work if target == "work" else self.editor
        git(root, "add", "-A")
        git(root, "commit", "--allow-empty", "-m", message)
        git(root, "push", "origin", "master")

    def remote_set_version(self, version, extra_files=None):
        git(self.editor, "pull", "origin", "master")
        files = {"pyproject.toml": self._pyproject(version)}
        if extra_files:
            files.update(extra_files)
        self._write(self.editor, files)
        self.commit(f"release {version}", target="editor")

    def head(self):
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.work,
            check=True, capture_output=True, text=True,
            env={**os.environ, **GIT_ENV},
        )
        return out.stdout.strip()

    def read(self, name):
        with open(os.path.join(self.work, name)) as handle:
            return handle.read()

    def worktree_clean(self):
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.work,
            capture_output=True, text=True,
            env={**os.environ, **GIT_ENV},
        )
        return result.stdout.strip() == ""

    def env(self, **overrides):
        base = {
            **os.environ,
            **GIT_ENV,
            "WEBAI_ROOT": self.work,
            "WEBAI_PID_FILE": self.pid_file,
            "WEBAI_LOG_FILE": self.log_file,
            "WEBAI_LOCK_FILE": self.lock_file,
            "RUNTIME_DIR": self.runtime_dir,
            "WEBAI_HEALTH_TIMEOUT": "20",
            "WEBAI_HEALTH_INTERVAL": "0.2",
        }
        base.update({k: str(v) for k, v in overrides.items()})
        return base

    def run_updater(self, args, extra_env=None, timeout=90):
        env = self.env(**(extra_env or {}))
        return subprocess.run(
            [sys.executable, UPDATE_PY, *args],
            cwd=self.work,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )




def spawn_service(repo, command, tracked, ready_url=None, ready_timeout=30.0,
                  env=None):
    """Spawn a detached-style service like the updater does; returns Popen.

    env=None inherits the test process environment. Pass repo.env() when the
    server must resolve the same RUNTIME_DIR as the updater (Windows IPC).
    """
    log = open(repo.log_file, "ab")
    popen_kwargs = {}
    if os.name == "nt":
        import subprocess as _sp

        popen_kwargs["creationflags"] = (
            _sp.CREATE_NEW_PROCESS_GROUP | _sp.CREATE_NO_WINDOW
        )
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        command,
        cwd=repo.work,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        **popen_kwargs,
    )
    log.close()
    tracked(proc)
    if ready_url and not wait_health(
        ready_url, timeout=ready_timeout, process=proc
    ):
        with open(repo.log_file, encoding="utf-8", errors="replace") as handle:
            log_contents = handle.read()
        raise RuntimeError(
            "service did not become healthy; "
            f"process_exit={proc.poll()!r}; log:\n{log_contents}"
        )
    return proc


def stub_start_command(port):
    return f'"{sys.executable}" "{SERVICE_STUB}" {port}'


def read_pid(repo):
    with open(repo.pid_file) as handle:
        return int(handle.read().strip())


_PLATFORM_MODULE = None


def _production_platform():
    """Load scripts/update_platform.py once (single source of liveness)."""
    global _PLATFORM_MODULE
    if _PLATFORM_MODULE is None:
        import importlib.util

        path = os.path.join(REPO_ROOT, "scripts", "update_platform.py")
        spec = importlib.util.spec_from_file_location(
            "update_platform_integration", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PLATFORM_MODULE = module
    return _PLATFORM_MODULE


def pid_alive(pid):
    """Production semantics everywhere: delegate to update_platform.

    No duplicate Win32 ctypes code lives in the harness.
    """
    if os.name == "nt":
        return _production_platform().pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _is_zombie(pid):
    """POSIX-only zombie probe: unreaped children of THIS test process."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return False


def wait_pid_gone(pid, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid) or _is_zombie(pid):
            return True
        time.sleep(0.1)
    return False


def wait_process_exit(process, timeout=20.0):
    """Reap a subprocess owned by this test, avoiding PID/zombie ambiguity."""
    try:
        process.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def cleanup_managed_service(
    repo, service_url, service_port, extra_ports=(), process=None
):
    """Stop updater-created service and verify all owned state is gone."""
    try:
        pid = read_pid(repo)
    except (OSError, ValueError):
        pid = None

    if os.path.exists(repo.pid_file):
        stopped = repo.run_updater(
            ["--stop"],
            extra_env={"WEBAI_HEALTH_URL": service_url},
            timeout=60,
        )
        if stopped.returncode != 0:
            raise AssertionError(
                "cleanup updater --stop failed:\n"
                f"{stopped.stdout}{stopped.stderr}"
            )

    if process is not None and (pid is None or pid == process.pid):
        assert wait_process_exit(process)
    elif pid is not None:
        assert wait_pid_gone(pid)
    assert not os.path.exists(repo.pid_file)
    for port in dict.fromkeys((service_port, *extra_ports)):
        assert wait_port_closed(port), (
            f"service endpoint still serving on port {port}"
        )
