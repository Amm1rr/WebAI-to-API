import asyncio
import importlib
import sys
from unittest.mock import MagicMock

import pytest

from app.utils import update_check


class Lock:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


def git_responses(*responses):
    calls = []

    async def fake_git(deadline, *args):
        calls.append(args)
        return responses[len(calls) - 1]

    return fake_git, calls


@pytest.fixture(autouse=True)
def local_version(mocker):
    return mocker.patch.object(update_check, "_read_local_version", return_value="1.0")


def test_update_platform_import_is_cached_without_sys_path_mutation():
    before = list(sys.path)
    platform_module = update_check._update_platform

    importlib.reload(update_check)

    assert sys.path == before
    assert update_check._update_platform is platform_module
    assert update_check.acquire_lock is platform_module.acquire_lock


@pytest.mark.asyncio
async def test_same_version_logs_up_to_date(mocker):
    lock = Lock()
    fake_git, calls = git_responses(
        (0, "master\n", ""),
        (0, "origin\n", ""),
        (0, "", ""),
        (0, "", ""),
        (0, '[project]\nversion = "1.0"\n', ""),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=lock)
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.info.assert_called_once_with("WebAI-to-API is up to date (v%s).", "1.0")
    assert lock.released is True
    assert all(command[0] in {"symbolic-ref", "remote", "fetch", "merge-base", "show"} for command in calls)


@pytest.mark.asyncio
async def test_unequal_versions_logs_host_update_command(mocker):
    fake_git, _ = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (0, "", ""),
        (0, "", ""), (0, '[project]\nversion = "2.0.dev1"\n', ""),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.info.assert_called_once_with(
        "Update available: v%s (current: v%s). Run %s to update.",
        "2.0.dev1", "1.0", "update-windows.cmd" if update_check.IS_WINDOWS else "./update-linux-macos.sh",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["feature\n", ""])
async def test_non_master_skips_without_fetch(mocker, branch):
    fake_git, calls = git_responses((0 if branch else 1, branch, ""))
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.debug.assert_called_once_with(
        "Update check skipped: local checkout is not a comparable master revision."
    )
    assert calls == [("symbolic-ref", "--short", "HEAD")]


@pytest.mark.asyncio
async def test_docker_skips_without_lock_or_git(mocker):
    lock = mocker.patch.object(update_check, "acquire_lock")
    git = mocker.patch.object(update_check, "_git")
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: True)

    logger.debug.assert_called_once_with("Update check skipped inside Docker.")
    lock.assert_not_called()
    git.assert_not_called()


@pytest.mark.asyncio
async def test_lock_contention_skips_immediately(mocker):
    mocker.patch.object(update_check, "acquire_lock", return_value=None)
    git = mocker.patch.object(update_check, "_git")
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.debug.assert_called_once_with(
        "Update check skipped: another update operation or update check is in progress."
    )
    git.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_failure_warns_and_releases_lock(mocker):
    lock = Lock()
    fake_git, _ = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (1, "", "offline"),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=lock)
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.warning.assert_called_once_with("Update check failed: %s", "git fetch failed: offline")
    assert lock.released is True


@pytest.mark.asyncio
async def test_missing_origin_skips(mocker):
    fake_git, calls = git_responses((0, "master\n", ""), (1, "", ""))
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.debug.assert_called_once_with("Update check skipped: no origin remote is configured.")
    assert ("fetch", "origin", "master") not in calls


@pytest.mark.asyncio
async def test_malformed_local_version_warns(mocker, local_version):
    fake_git, _ = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (0, "", ""),
        (0, "", ""),
    )
    local_version.side_effect = update_check.UpdateCheckError(
        "No readable [project].version in local pyproject.toml."
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.warning.assert_called_once_with(
        "Update check failed: %s", "No readable [project].version in local pyproject.toml."
    )


@pytest.mark.asyncio
async def test_git_unavailable_logs_info_skip(mocker):
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(
        update_check,
        "_git",
        side_effect=update_check.GitUnavailableError("Git is not available."),
    )
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.info.assert_called_once_with("Update check skipped: Git is not available.")
    logger.warning.assert_not_called()
    logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_git_permission_failure_remains_warning(mocker):
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(
        update_check,
        "_git",
        side_effect=update_check.UpdateCheckError(
            "Cannot execute git: permission denied. Is Git installed and on PATH?"
        ),
    )
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.warning.assert_called_once_with(
        "Update check failed: %s",
        "Cannot execute git: permission denied. Is Git installed and on PATH?",
    )
    logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_missing_git_executable_is_classified_narrowly(mocker):
    mocker.patch.object(
        update_check.asyncio,
        "create_subprocess_exec",
        side_effect=FileNotFoundError("git not found"),
    )

    with pytest.raises(update_check.GitUnavailableError, match="Git is not available"):
        await update_check._git(
            asyncio.get_running_loop().time() + update_check.CHECK_TIMEOUT_SECONDS,
            "--version",
        )


@pytest.mark.asyncio
async def test_timeout_warns(mocker):
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=update_check.UpdateCheckError("Update check timed out."))
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.warning.assert_called_once_with("Update check failed: %s", "Update check timed out.")


@pytest.mark.asyncio
async def test_diverged_master_skips(mocker):
    fake_git, calls = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (0, "", ""), (1, "", ""),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.debug.assert_called_once_with(
        "Update check skipped: local checkout is not a comparable master revision."
    )
    assert ("show", "HEAD:pyproject.toml") not in calls


@pytest.mark.asyncio
async def test_malformed_remote_version_warns(mocker):
    fake_git, _ = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (0, "", ""),
        (0, "", ""), (0, "[project\n", ""),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.warning.assert_called_once_with(
        "Update check failed: %s", "No readable [project].version in origin/master:pyproject.toml."
    )


@pytest.mark.asyncio
async def test_missing_remote_version_warns(mocker):
    fake_git, _ = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (0, "", ""),
        (0, "", ""), (0, "[project]\nname = \"webai-to-api\"\n", ""),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.warning.assert_called_once_with(
        "Update check failed: %s", "No readable [project].version in origin/master:pyproject.toml."
    )


@pytest.mark.asyncio
async def test_cancellation_releases_lock(mocker):
    lock = Lock()
    entered = asyncio.Event()

    async def slow_git(deadline, *args):
        entered.set()
        await asyncio.Future()

    mocker.patch.object(update_check, "acquire_lock", return_value=lock)
    mocker.patch.object(update_check, "_git", side_effect=slow_git)
    task = asyncio.create_task(update_check.run_update_check(exists=lambda _: False))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert lock.released is True


@pytest.mark.asyncio
async def test_git_cancellation_terminates_active_subprocess(mocker):
    entered = asyncio.Event()
    process = MagicMock(returncode=None)

    async def communicate():
        entered.set()
        await asyncio.Future()

    process.communicate.side_effect = communicate
    process.wait = mocker.AsyncMock(return_value=None)
    mocker.patch.object(
        update_check.asyncio,
        "create_subprocess_exec",
        new=mocker.AsyncMock(return_value=process),
    )
    task = asyncio.create_task(
        update_check._git(asyncio.get_running_loop().time() + 10, "fetch", "origin", "master")
    )
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_working_tree_version_source(mocker, local_version, tmp_path, monkeypatch):
    mocker.stop(local_version)
    monkeypatch.setattr(update_check, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "1.1"\n', encoding="utf-8"
    )
    fake_git, _ = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (0, "", ""),
        (0, "", ""), (0, '[project]\nversion = "1.1"\n', ""),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.info.assert_called_once_with("WebAI-to-API is up to date (v%s).", "1.1")


@pytest.mark.asyncio
async def test_malformed_working_tree_version_warns_without_normal_result(
    mocker, local_version, tmp_path, monkeypatch
):
    mocker.stop(local_version)
    monkeypatch.setattr(update_check, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    fake_git, _ = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (0, "", ""), (0, "", ""),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.warning.assert_called_once()
    logger.info.assert_not_called()


@pytest.mark.asyncio
async def test_missing_working_tree_version_warns_without_normal_result(
    mocker, local_version, tmp_path, monkeypatch
):
    mocker.stop(local_version)
    monkeypatch.setattr(update_check, "ROOT", tmp_path)
    fake_git, _ = git_responses(
        (0, "master\n", ""), (0, "origin\n", ""), (0, "", ""), (0, "", ""),
    )
    mocker.patch.object(update_check, "acquire_lock", return_value=Lock())
    mocker.patch.object(update_check, "_git", side_effect=fake_git)
    logger = MagicMock()

    await update_check.run_update_check(logger=logger, exists=lambda _: False)

    logger.warning.assert_called_once()
    logger.info.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows only")
def test_windows_proactor_runs_real_subprocess():
    policy = asyncio.WindowsProactorEventLoopPolicy()
    loop = policy.new_event_loop()
    try:
        asyncio.set_event_loop(loop)

        async def run_child():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "print('ok', flush=True)",
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            return process.returncode, stdout.decode().strip()

        assert loop.run_until_complete(run_child()) == (0, "ok")
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows only")
def test_checker_git_subprocess_runs_under_proactor():
    policy = asyncio.WindowsProactorEventLoopPolicy()
    loop = policy.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        code, stdout, _ = loop.run_until_complete(
            update_check._git(
                loop.time() + update_check.CHECK_TIMEOUT_SECONDS,
                "--version",
            )
        )
        assert code == 0
        assert stdout.lower().startswith("git version")
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows only")
def test_windows_proactor_terminates_real_child():
    policy = asyncio.WindowsProactorEventLoopPolicy()
    loop = policy.new_event_loop()
    try:
        asyncio.set_event_loop(loop)

        async def run_child():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-u",
                "-c",
                "import time; print('ready', flush=True); time.sleep(60)",
                stdout=asyncio.subprocess.PIPE,
            )
            assert (await process.stdout.readline()).strip() == b"ready"
            await update_check._terminate_process(process)
            return process.returncode

        assert loop.run_until_complete(run_child()) is not None
    finally:
        loop.close()
        asyncio.set_event_loop(None)
