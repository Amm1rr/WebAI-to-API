#!/usr/bin/env python3
"""
Git-based updater for WebAI-to-API.

The [project].version field in pyproject.toml is the update trigger;
origin/master is the transport.
Host-only: refuses to run inside containers. Never touches user-owned paths
(.env, .env.local, config.conf, runtime/).

Rollback covers code and dependencies only; persistent-state schema changes
introduced by a newer version are not reverted.
"""

import sys

# Python contract guard. Must run before imports that require the supported
# interpreter (e.g. stdlib tomllib, 3.11+), so unsupported Pythons get a
# clean updater error instead of a raw import traceback.
SUPPORTED_PYTHON_RANGE_TEXT = ">=3.11,<3.13"


def _python_version_supported(version_info):
    return (3, 11) <= version_info[:2] < (3, 13)


if not _python_version_supported(sys.version_info):
    print(
        "ERROR: WebAI-to-API updater requires Python "
        f"{SUPPORTED_PYTHON_RANGE_TEXT}. "
        f"Current version is {sys.version_info[0]}.{sys.version_info[1]}.",
        file=sys.stderr,
    )
    raise SystemExit(1)

import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
import tomllib
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from update_platform import (
    IS_WINDOWS,
    PlatformOperationError,
    acquire_lock as platform_acquire_lock,
    force_kill as platform_force_kill,
    pid_alive as platform_pid_alive,
    spawn_detached as platform_spawn_detached,
    terminate_graceful as platform_terminate_graceful,
)


def _temp_base():
    """POSIX keeps the historical /tmp defaults byte-for-byte; Windows uses
    the standard per-user temp directory."""
    return "/tmp" if not IS_WINDOWS else tempfile.gettempdir()


ROOT = os.environ.get("WEBAI_ROOT") or os.path.dirname(SCRIPT_DIR)
PID_FILE = os.environ.get(
    "WEBAI_PID_FILE", os.path.join(_temp_base(), "webai-to-api.pid")
)
LOG_FILE = os.environ.get(
    "WEBAI_LOG_FILE", os.path.join(_temp_base(), "webai-to-api.log")
)
LOCK_FILE = os.environ.get(
    "WEBAI_LOCK_FILE", os.path.join(_temp_base(), "webai-to-api-update.lock")
)
SHUTDOWN_CONTROL_FILE = os.path.join(
    os.environ.get("RUNTIME_DIR") or os.path.join(ROOT, "runtime"),
    "shutdown-control.json",
)
HEALTH_URL = os.environ.get("WEBAI_HEALTH_URL", "http://127.0.0.1:6969/health")
HEALTH_TIMEOUT = float(os.environ.get("WEBAI_HEALTH_TIMEOUT", "60"))
HEALTH_INTERVAL = float(os.environ.get("WEBAI_HEALTH_INTERVAL", "2"))
START_COMMAND = os.environ.get("WEBAI_START_COMMAND", "poetry run python src/run.py")
POETRY_COMMAND = os.environ.get("WEBAI_POETRY", "poetry")
BRANCH = "master"
PYPROJECT_FILE = "pyproject.toml"
LOCK_FILE_NAME = "poetry.lock"
PROTECTED_PATHS = (".env", ".env.local", "config.conf", "runtime/")

UPDATE_LOCKED_MESSAGE = (
    "Another updater instance is already running. If this is stale, remove "
    f"{LOCK_FILE} and retry."
)
DOCKER_MESSAGE = (
    "This updater manages host installations only. Inside Docker, update with: "
    "git pull && docker compose up -d --build"
)


class UpdateError(Exception):
    """Fatal updater error; message is user-facing."""


def run(cmd, check=True):
    try:
        result = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True
        )
    except OSError as error:
        raise UpdateError(
            f"Cannot execute '{cmd[0]}': {error.strerror or error}. "
            "Is the required tool installed and on PATH?"
        ) from error
    if check and result.returncode != 0:
        raise UpdateError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def git(*args, check=True):
    return run(["git", *args], check=check)


def say(message):
    print(f"--> {message}", flush=True)


def die(message, code=1):
    print(f"ERROR: {message}", file=sys.stderr, flush=True)
    sys.exit(code)


def running_service_pid():
    """PID from PID_FILE when that process is alive; stale files yield None."""
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        return None
    return pid if platform_pid_alive(pid) else None


_SEND_SHUTDOWN = None

# Phase 4 client default; the per-call timeout is capped by remaining grace
# budget so one IPC attempt can never stretch the stop beyond ~10s wall clock.
WINDOWS_IPC_TIMEOUT_SECONDS = 3.0
WINDOWS_STOP_BUDGET_SECONDS = 10.0


def _send_windows_shutdown(control_file, timeout=None):
    """Lazy-bound Phase 4 transport client (Windows graceful stop only)."""
    global _SEND_SHUTDOWN
    if _SEND_SHUTDOWN is None:
        src_dir = os.path.join(os.path.dirname(SCRIPT_DIR), "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from app.shutdown_transport import (
            ShutdownTransportError as transport_error,
            send_shutdown as send,
        )

        globals()["ShutdownTransportError"] = transport_error
        _SEND_SHUTDOWN = send
    return _SEND_SHUTDOWN(control_file, timeout=timeout)


# Normalized Windows IPC outcomes; "unreachable" covers missing/stale/malformed
# control metadata and connect failures. All are retryable by policy.
_WINDOWS_IPC_RETRY = "retry"
_WINDOWS_IPC_UNREACHABLE = "unreachable"


def request_service_shutdown(pid, timeout=None):
    """
    One platform-dispatched graceful-stop request.

    POSIX: deliver SIGTERM via the platform primitive (raises
    PlatformOperationError on operational failure, as before).
    Windows: attempt the Phase 4 loopback IPC; returns "ok" (accepted),
    "retry" (not accepted yet), or "unreachable" (no usable control
    channel). Never raises for absent/stale channels — the grace budget
    owns retries and the force fallback. `timeout` caps this single IPC
    call; callers on the Windows stop path pass min(IPC default,
    remaining budget).
    """
    if not IS_WINDOWS:
        platform_terminate_graceful(pid)
        return None
    try:
        result = _send_windows_shutdown(SHUTDOWN_CONTROL_FILE, timeout)
    except ShutdownTransportError:
        return _WINDOWS_IPC_UNREACHABLE
    if result == "ok":
        return "ok"
    return _WINDOWS_IPC_RETRY


def stop_service(pid):
    say(f"Stopping WebAI-to-API (PID {pid})...")
    if IS_WINDOWS:
        _stop_service_windows(pid)
    else:
        _stop_service_posix(pid)
    try:
        os.unlink(PID_FILE)
    except FileNotFoundError:
        pass
    except OSError as error:
        # Service is down but lifecycle state is uncertain: fail loudly.
        raise UpdateError(
            f"Service stopped but stale PID file {PID_FILE} "
            f"could not be removed: {error}"
        ) from error


def _stop_service_posix(pid):
    try:
        platform_terminate_graceful(pid)
    except PlatformOperationError as error:
        raise UpdateError(f"Cannot signal service PID {pid}: {error}") from error
    for _ in range(10):
        if not platform_pid_alive(pid):
            break
        time.sleep(1)
    else:
        say("Process did not stop gracefully; forcing termination.")
        try:
            platform_force_kill(pid)
        except PlatformOperationError as error:
            raise UpdateError(
                f"Cannot force-kill service PID {pid}: {error}"
            ) from error


def _stop_service_windows(pid):
    # Single ~10s wall-clock budget (monotonic): IPC ticks interleave with
    # liveness polls, each IPC call's timeout is capped by the remaining
    # budget so it can never stretch the stop. "ok" stops further sends;
    # process exit is the only success proof; hard kill is fallback only.
    deadline = time.monotonic() + WINDOWS_STOP_BUDGET_SECONDS
    accepted = False
    while time.monotonic() < deadline:
        if not platform_pid_alive(pid):
            return
        if not accepted:
            ipc_timeout = min(
                WINDOWS_IPC_TIMEOUT_SECONDS,
                max(0.0, deadline - time.monotonic()),
            )
            accepted = (
                request_service_shutdown(pid, timeout=ipc_timeout) == "ok"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))
    say("Process did not stop gracefully; forcing termination.")
    try:
        platform_force_kill(pid)
    except PlatformOperationError as error:
        raise UpdateError(
            f"Cannot force-kill service PID {pid}: {error}"
        ) from error


def _strip_matching_double_quotes(token):
    if len(token) >= 2 and token[0] == token[-1] == '"':
        return token[1:-1]
    return token


def parse_start_command():
    """Parse START_COMMAND; malformed or empty commands are fatal.

    POSIX keeps POSIX-mode shlex parsing unchanged. Windows parses in
    non-POSIX mode (preserving backslashes and drive paths), strips one
    matching pair of surrounding double quotes per token, and resolves
    argv[0] via shutil.which() so PATHEXT (.exe/.cmd/.bat) works with
    shell=False. Resolution failure is a clean fatal error.
    """
    if not START_COMMAND.strip():
        raise UpdateError("START_COMMAND is empty; cannot start the service.")
    try:
        if IS_WINDOWS:
            tokens = [
                _strip_matching_double_quotes(token)
                for token in shlex.split(START_COMMAND, posix=False)
            ]
            resolved = shutil.which(tokens[0])
            if resolved is None:
                raise UpdateError(
                    f"Cannot resolve executable {tokens[0]!r} from "
                    f"START_COMMAND {START_COMMAND!r}."
                )
            tokens[0] = resolved
            return tokens
        return shlex.split(START_COMMAND)
    except ValueError as error:
        raise UpdateError(
            f"Malformed START_COMMAND {START_COMMAND!r}: {error}"
        ) from error


def start_service():
    say("Starting WebAI-to-API in the background...")
    argv = parse_start_command()
    try:
        log = open(LOG_FILE, "ab")
    except OSError as error:
        raise UpdateError(
            f"Cannot start service with '{START_COMMAND}': "
            f"{error.strerror or error}"
        ) from error
    try:
        process = platform_spawn_detached(argv, ROOT, log)
    except PlatformOperationError as error:
        raise UpdateError(
            f"Cannot start service with '{START_COMMAND}': {error.user_message}"
        ) from error
    try:
        with open(PID_FILE, "w") as handle:
            handle.write(str(process.pid))
    except OSError as error:
        process.kill()
        raise UpdateError(f"Cannot write PID file {PID_FILE}: {error}") from error
    say(f"Started (PID {process.pid}); logs at {LOG_FILE}")
    return process


def health_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for_health(url, timeout, interval):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if health_ok(url):
            return True
        time.sleep(interval)
    return health_ok(url)


def container_guard(exists=None):
    # exists resolved at call time so tests can simulate /.dockerenv.
    if (exists or os.path.exists)("/.dockerenv"):
        raise UpdateError(DOCKER_MESSAGE)


def _extract_project_version(raw):
    """Parse pyproject TOML and return [project].version; '' on any failure."""
    try:
        data = tomllib.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        version = data.get("project", {}).get("version", "")
        return str(version).strip() if version else ""
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, AttributeError, TypeError):
        return ""


def read_local_version():
    try:
        with open(os.path.join(ROOT, PYPROJECT_FILE), "rb") as handle:
            return _extract_project_version(handle.read())
    except OSError:
        return ""


def _is_protected_path(path):
    """Exact-match user files; runtime/ protects the whole subtree."""
    if path in (".env", ".env.local", "config.conf"):
        return True
    return path == "runtime" or path.startswith("runtime/")


def _local_obstructions(remote_files, head_tracked):
    """
    Untracked/ignored local paths that would obstruct checkout of
    origin/master. For every remotely tracked path not already tracked by
    HEAD, checks the path itself and every filesystem ancestor below the
    repo root:

    - exact path exists locally            -> collision (would be clobbered)
    - ancestor exists as a non-directory   -> collision (blocks dir creation)
    - ancestor is a directory              -> fine, keep descending

    Deterministic, read-only, covers nested/untracked/ignored layouts.
    Symlinks never count as safe directory containers: any symlink at an
    exact or ancestor position is an obstruction unless HEAD already tracks
    it (then reset legitimately manages it). Targets are never followed.
    """
    reported = set()
    for path in sorted(remote_files):
        if path in head_tracked:
            continue
        parts = path.split("/")
        candidates = [path] + [
            "/".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)
        ]
        for candidate in candidates:
            if candidate in head_tracked or candidate in reported:
                # Tracked-by-HEAD objects are updated legitimately by reset.
                continue
            full = os.path.join(ROOT, candidate)
            try:
                st = os.lstat(full)
            except OSError:
                continue  # nothing on disk at this position
            if stat.S_ISLNK(st.st_mode):
                # A symlink is not a trustworthy container: it may point
                # anywhere and reset must never write through it.
                reported.add(candidate)
                continue
            if candidate != path:
                if stat.S_ISDIR(st.st_mode):
                    continue  # plain real directory: harmless container
                reported.add(candidate)
                continue
            # Exact path exists locally and is untracked -> collision,
            # regardless of file/dir/symlink type.
            reported.add(candidate)
    return reported


def preflight():
    """All checks before touching service or worktree.

    Returns dict with remote version, previous SHA and dependency flags.
    """
    inside = git("rev-parse", "--is-inside-work-tree", check=False).stdout.strip()
    if inside != "true":
        raise UpdateError("Not a Git repository; cannot update.")

    branch = git("symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
    if branch != BRANCH:
        raise UpdateError(
            f"Updater requires branch '{BRANCH}' (found: '{branch or 'detached HEAD'}')."
        )
    if not git("remote", "get-url", "origin", check=False).stdout.strip():
        raise UpdateError("No 'origin' remote configured.")

    say("Fetching origin/master...")
    fetch = git("fetch", "origin", BRANCH, check=False)
    if fetch.returncode != 0:
        raise UpdateError(f"git fetch failed:\n{fetch.stderr.strip()}")

    show = git("show", f"origin/{BRANCH}:{PYPROJECT_FILE}", check=False)
    remote_version = _extract_project_version(show.stdout) if show.returncode == 0 else ""
    if not remote_version:
        raise UpdateError(
            f"No readable [project].version in origin/master:{PYPROJECT_FILE}; "
            "refusing to update."
        )

    local_version = read_local_version()
    if local_version and local_version == remote_version:
        say(f"Already up to date (project version {local_version}).")
        return None

    say(f"Update available: {local_version or '(unknown)'} -> {remote_version}")

    ancestry = git("merge-base", "--is-ancestor", "HEAD", f"origin/{BRANCH}", check=False)
    if ancestry.returncode != 0:
        raise UpdateError(
            "Current HEAD has local commits not on origin/master. "
            "Resolve divergence manually; the updater will not discard commits."
        )

    dirty = git(
        "status", "--porcelain", "--untracked-files=no", check=False
    ).stdout.splitlines()
    if dirty:
        listing = "\n".join(f"  {line}" for line in dirty[:20])
        raise UpdateError(
            "Worktree has tracked/staged modifications; resolve them first:\n" + listing
        )

    remote_files = set(
        git("ls-tree", "-r", "--name-only", f"origin/{BRANCH}").stdout.splitlines()
    )
    protected_hits = sorted(
        path for path in remote_files if _is_protected_path(path)
    )
    if protected_hits:
        raise UpdateError(
            "origin/master would track protected user-owned paths "
            f"({', '.join(protected_hits)}); refusing to update."
        )

    head_tracked = set(
        git("ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    )
    collisions = sorted(_local_obstructions(remote_files, head_tracked))
    if collisions:
        raise UpdateError(
            "Update would overwrite these untracked/ignored local files:\n"
            + "\n".join(f"  {path}" for path in collisions[:20])
            + "\nMove or remove them first."
        )

    changed = set(git("diff", "--name-only", "HEAD", f"origin/{BRANCH}").stdout.split())
    lock_changed = LOCK_FILE_NAME in changed

    old_toml = git("show", f"HEAD:{PYPROJECT_FILE}", check=False).stdout
    new_toml = git("show", f"origin/{BRANCH}:{PYPROJECT_FILE}", check=False).stdout
    old_signature = _dependency_signature(old_toml)
    new_signature = _dependency_signature(new_toml)
    if old_signature is None or new_signature is None:
        # Fail safe: unparseable dependency metadata -> assume deps changed.
        deps_changed = True
    else:
        deps_changed = old_signature != new_signature
    if lock_changed:
        deps_changed = True

    return {
        "previous_sha": git("rev-parse", "HEAD").stdout.strip(),
        "deps_changed": deps_changed,
        "lock_changed": lock_changed,
    }


def _dependency_signature(toml_text):
    """
    Deterministic fingerprint of dependency-bearing pyproject configuration.
    Dicts compare order-insensitively (sorted JSON); list order is preserved
    (dependency lists are semantically ordered). Returns None when the
    document cannot be parsed, signalling a conservative 'changed'.
    """
    try:
        data = tomllib.loads(toml_text)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}

    signature = {
        "project.requires-python": project.get("requires-python"),
        "project.dependencies": project.get("dependencies"),
        "project.optional-dependencies": project.get("optional-dependencies"),
        "dependency-groups": data.get("dependency-groups"),
        "tool.poetry.dependencies": poetry.get("dependencies"),
        "tool.poetry.group": poetry.get("group"),
        "tool.poetry.dev-dependencies": poetry.get("dev-dependencies"),
    }
    return json.dumps(signature, sort_keys=True, default=repr)


def sync_dependencies(lock_changed):
    say("Syncing dependencies (poetry install --sync)...")
    run([POETRY_COMMAND, "install", "--sync"])
    if lock_changed:
        say("Installing Chromium for updated Playwright...")
        run([POETRY_COMMAND, "run", "playwright", "install", "chromium"])


def perform_rollback(previous_sha, deps_changed, lock_changed, was_running):
    print("ERROR: update failed; rolling back.", file=sys.stderr, flush=True)
    pid = running_service_pid()
    if pid:
        stop_service(pid)

    # Fail closed: restart only after code AND dependency restoration
    # succeeded. Lock release is owned exclusively by main()'s finally.
    try:
        git("reset", "--hard", previous_sha)
        if deps_changed:
            sync_dependencies(lock_changed)
    except UpdateError as error:
        die(
            "ROLLBACK FAILED: could not restore the previous code/dependency "
            f"state; service left STOPPED.\n{error}\nServer log: {LOG_FILE}"
        )

    if was_running:
        try:
            start_service()
        except UpdateError as error:
            die(
                "ROLLBACK FAILED: previous code/dependencies restored, but "
                "the service could not be restarted; service left STOPPED.\n"
                f"{error}\nServer log: {LOG_FILE}"
            )
        if not wait_for_health(HEALTH_URL, HEALTH_TIMEOUT, HEALTH_INTERVAL):
            die(
                "ROLLBACK FAILED: restored version did not become healthy; "
                "service was started from the previous commit but may be "
                f"degraded.\nServer log: {LOG_FILE}"
            )

    die(
        "Rolled back to previous commit successfully; service restored.\n"
        f"Check server log: {LOG_FILE}",
        code=1,
    )


def update_once():
    plan = preflight()
    if plan is None:
        return 0

    # Single liveness query: the PID captured here is authoritative for the
    # whole update; no re-query before stop (TOCTOU-safe).
    service_pid = running_service_pid()
    was_running = service_pid is not None
    previous_sha = plan["previous_sha"]

    if service_pid is not None:
        stop_service(service_pid)

    try:
        say("Moving worktree to origin/master...")
        git("reset", "--hard", "origin/master")
        if plan["deps_changed"]:
            sync_dependencies(plan["lock_changed"])

        if was_running:
            start_service()
            say("Waiting for /health ...")
            if not wait_for_health(HEALTH_URL, HEALTH_TIMEOUT, HEALTH_INTERVAL):
                raise UpdateError("Service did not become healthy after update.")
        say("Update complete.")
        return 0
    except UpdateError as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        perform_rollback(previous_sha, plan["deps_changed"], plan["lock_changed"], was_running)


def stop_command():
    pid = running_service_pid()
    if pid:
        stop_service(pid)
        say("WebAI-to-API stopped.")
        return 0
    if os.path.exists(PID_FILE):
        # Stale/invalid PID file: clean it up so the next start is unambiguous.
        try:
            os.unlink(PID_FILE)
        except OSError as error:
            die(f"Cannot remove stale PID file {PID_FILE}: {error}")
        say("Removed stale PID file; WebAI-to-API was not running.")
        return 0
    say("WebAI-to-API is not running.")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Lock first: both flows mutually exclude through the same flock.
    try:
        lock_handle = platform_acquire_lock(LOCK_FILE)
    except PlatformOperationError as error:
        prefix = {
            "open": f"Cannot open updater lock file {LOCK_FILE}: ",
            "flock": f"Cannot lock updater lock file {LOCK_FILE}: ",
        }.get(getattr(error, "phase", ""), "")
        die(f"{prefix}{error}")
    if lock_handle is None:
        print(UPDATE_LOCKED_MESSAGE, file=sys.stderr)
        return 0

    try:
        if argv and argv[0] == "--stop":
            # Explicit stop stays available inside Docker; the host-only
            # restriction applies to update orchestration exclusively.
            return stop_command()

        container_guard()
        return update_once()
    except UpdateError as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        lock_handle.release()


if __name__ == "__main__":
    sys.exit(main())
