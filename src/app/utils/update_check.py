"""Read-only, bounded startup notification for host-install updates."""

import asyncio
import importlib.util
import logging
import os
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_UPDATE_PLATFORM_MODULE_NAME = "_webai_to_api_update_platform"


def _load_update_platform():
    module_path = ROOT / "scripts" / "update_platform.py"
    existing = sys.modules.get(_UPDATE_PLATFORM_MODULE_NAME)
    if existing is not None:
        if Path(existing.__file__).resolve() != module_path.resolve():
            raise ImportError(
                f"Cached update platform module has unexpected path: {existing.__file__}"
            )
        return existing

    spec = importlib.util.spec_from_file_location(
        _UPDATE_PLATFORM_MODULE_NAME, module_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load update platform module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_UPDATE_PLATFORM_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except OSError as error:
        sys.modules.pop(_UPDATE_PLATFORM_MODULE_NAME, None)
        raise ImportError(
            f"Cannot load update platform module from {module_path}: {error}"
        ) from error
    except Exception:
        sys.modules.pop(_UPDATE_PLATFORM_MODULE_NAME, None)
        raise
    return module


_update_platform = _load_update_platform()
IS_WINDOWS = _update_platform.IS_WINDOWS
PlatformOperationError = _update_platform.PlatformOperationError
acquire_lock = _update_platform.acquire_lock

BRANCH = "master"
PYPROJECT_FILE = "pyproject.toml"
CHECK_TIMEOUT_SECONDS = 10.0
LOCK_FILE = os.environ.get(
    "WEBAI_LOCK_FILE",
    os.path.join("/tmp" if not IS_WINDOWS else tempfile.gettempdir(), "webai-to-api-update.lock"),
)


class UpdateCheckError(Exception):
    """Expected update-check failure suitable for a concise warning."""


class GitUnavailableError(UpdateCheckError):
    """Git executable is missing from the runtime environment."""


def _extract_project_version(raw: str | bytes) -> str:
    try:
        data = tomllib.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        version = data.get("project", {}).get("version", "")
        return str(version).strip() if version else ""
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, AttributeError, TypeError):
        return ""


def _read_local_version() -> str:
    try:
        raw = (ROOT / PYPROJECT_FILE).read_bytes()
    except OSError as error:
        raise UpdateCheckError(
            f"Cannot read local {PYPROJECT_FILE}: {error.strerror or error}"
        ) from error
    version = _extract_project_version(raw)
    if not version:
        raise UpdateCheckError(
            f"No readable [project].version in local {PYPROJECT_FILE}."
        )
    return version


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _git(deadline: float, *args: str) -> tuple[int, str, str]:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise UpdateCheckError("Update check timed out.")
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise GitUnavailableError(
            "Git is not available."
        ) from error
    except OSError as error:
        raise UpdateCheckError(
            f"Cannot execute git: {error.strerror or error}. Is Git installed and on PATH?"
        ) from error
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=remaining)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        await _terminate_process(process)
        if asyncio.current_task().cancelling():
            raise
        raise UpdateCheckError("Update check timed out.")
    return process.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


async def _check(deadline: float, exists=os.path.exists) -> tuple[str, str, str | None]:
    if exists("/.dockerenv"):
        return "skip", "Update check skipped inside Docker.", None

    try:
        lock = acquire_lock(LOCK_FILE)
    except PlatformOperationError as error:
        raise UpdateCheckError(f"Cannot acquire update lock: {error.user_message}") from error
    if lock is None:
        return "skip", "Update check skipped: another update operation or update check is in progress.", None

    try:
        code, branch, _ = await _git(deadline, "symbolic-ref", "--short", "HEAD")
        if code != 0 or branch.strip() != BRANCH:
            return "skip", "Update check skipped: local checkout is not a comparable master revision.", None

        code, origin, _ = await _git(deadline, "remote", "get-url", "origin")
        if code != 0 or not origin.strip():
            return "skip", "Update check skipped: no origin remote is configured.", None

        code, _, stderr = await _git(deadline, "fetch", "origin", BRANCH)
        if code != 0:
            raise UpdateCheckError(f"git fetch failed: {stderr.strip() or 'unknown error'}")

        code, _, _ = await _git(
            deadline, "merge-base", "--is-ancestor", "HEAD", f"origin/{BRANCH}"
        )
        if code == 1:
            return "skip", "Update check skipped: local checkout is not a comparable master revision.", None
        if code != 0:
            raise UpdateCheckError("Cannot determine whether local master is comparable to origin/master.")

        local_version = _read_local_version()

        code, remote_toml, _ = await _git(
            deadline, "show", f"origin/{BRANCH}:{PYPROJECT_FILE}"
        )
        remote_version = _extract_project_version(remote_toml) if code == 0 else ""
        if not remote_version:
            raise UpdateCheckError("No readable [project].version in origin/master:pyproject.toml.")
        return "result", local_version, remote_version
    finally:
        lock.release()


async def run_update_check(logger: logging.Logger | None = None, exists=os.path.exists) -> None:
    """Log one optional update notification; never raise expected failures."""
    logger = logger or logging.getLogger("app")
    deadline = asyncio.get_running_loop().time() + CHECK_TIMEOUT_SECONDS
    try:
        status, value, remote_version = await _check(deadline, exists=exists)
        if status == "skip":
            logger.debug(value)
        elif value == remote_version:
            logger.info("WebAI-to-API is up to date (v%s).", value)
        else:
            command = "update-windows.cmd" if IS_WINDOWS else "./update-linux-macos.sh"
            logger.info(
                "Update available: v%s (current: v%s). Run %s to update.",
                remote_version,
                value,
                command,
            )
    except asyncio.CancelledError:
        raise
    except GitUnavailableError:
        logger.info("Update check skipped: Git is not available.")
    except UpdateCheckError as error:
        logger.warning("Update check failed: %s", str(error))
    except Exception:
        logger.exception("Unexpected update-check failure.")
