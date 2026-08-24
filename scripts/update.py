#!/usr/bin/env python3
"""
Git-based updater for WebAI-to-API.

VERSION (repo root) is the update trigger; origin/master is the transport.
Host-only: refuses to run inside containers. Never touches user-owned paths
(.env, .env.local, config.conf, runtime/).

Rollback covers code and dependencies only; persistent-state schema changes
introduced by a newer version are not reverted.
"""

import errno
import fcntl
import os
import shlex
import signal
import stat
import subprocess
import sys
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("WEBAI_ROOT") or os.path.dirname(SCRIPT_DIR)
PID_FILE = os.environ.get("WEBAI_PID_FILE", "/tmp/webai-to-api.pid")
LOG_FILE = os.environ.get("WEBAI_LOG_FILE", "/tmp/webai-to-api.log")
LOCK_FILE = os.environ.get("WEBAI_LOCK_FILE", "/tmp/webai-to-api-update.lock")
HEALTH_URL = os.environ.get("WEBAI_HEALTH_URL", "http://127.0.0.1:6969/health")
HEALTH_TIMEOUT = float(os.environ.get("WEBAI_HEALTH_TIMEOUT", "60"))
HEALTH_INTERVAL = float(os.environ.get("WEBAI_HEALTH_INTERVAL", "2"))
START_COMMAND = os.environ.get("WEBAI_START_COMMAND", "poetry run python src/run.py")
POETRY_COMMAND = os.environ.get("WEBAI_POETRY", "poetry")
BRANCH = "master"
VERSION_FILE = "VERSION"
PROTECTED_PATHS = (".env", ".env.local", "config.conf", "runtime/")
DEP_FILES = ("pyproject.toml", "poetry.lock")

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


def _pid_alive(pid):
    """Zombie-aware liveness: a reaped-pending child must count as dead."""
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


_LOCK_FD = None


def acquire_lock():
    """
    Acquire an exclusive non-blocking flock on the lock file.
    Returns False only for expected lock contention (another updater owns
    it). Open failures and unexpected flock errors raise UpdateError.
    The fd stays open for the updater lifetime; the kernel releases on
    close, so stale locks and unlink races cannot occur.
    """
    global _LOCK_FD
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as error:
        raise UpdateError(
            f"Cannot open updater lock file {LOCK_FILE}: {error}"
        ) from error
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(fd)
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return False  # expected contention: another updater owns it
        raise UpdateError(
            f"Cannot lock updater lock file {LOCK_FILE}: {error}"
        ) from error
    _LOCK_FD = fd
    return True


def release_lock():
    global _LOCK_FD
    if _LOCK_FD is None:
        return
    try:
        fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(_LOCK_FD)
    except OSError:
        pass
    _LOCK_FD = None


def running_service_pid():
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def stop_service(pid):
    say(f"Stopping WebAI-to-API (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise UpdateError(f"Cannot signal service PID {pid}: {error}") from error
    for _ in range(10):
        if not _pid_alive(pid):
            break
        time.sleep(1)
    else:
        say("Process did not stop gracefully; forcing termination.")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise UpdateError(
                f"Cannot force-kill service PID {pid}: {error}"
            ) from error
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


def parse_start_command():
    """Parse START_COMMAND; malformed or empty commands are fatal."""
    if not START_COMMAND.strip():
        raise UpdateError("START_COMMAND is empty; cannot start the service.")
    try:
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
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as error:
        raise UpdateError(
            f"Cannot start service with '{START_COMMAND}': {error.strerror or error}"
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


def read_local_version():
    try:
        with open(os.path.join(ROOT, VERSION_FILE)) as handle:
            return handle.read().strip()
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

    show = git("show", f"origin/{BRANCH}:{VERSION_FILE}", check=False)
    remote_version = show.stdout.strip() if show.returncode == 0 else ""
    if not remote_version:
        raise UpdateError(
            f"No readable VERSION on origin/master; refusing to update."
        )

    local_version = read_local_version()
    if local_version and local_version == remote_version:
        say(f"Already up to date (VERSION {local_version}).")
        return None

    say(f"Update available: {local_version or '(none)'} -> {remote_version}")

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
    return {
        "previous_sha": git("rev-parse", "HEAD").stdout.strip(),
        "deps_changed": any(path in changed for path in DEP_FILES),
        "lock_changed": "poetry.lock" in changed,
    }


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
        locked = acquire_lock()
    except UpdateError as error:
        die(str(error))
    if not locked:
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
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
