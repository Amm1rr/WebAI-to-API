import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"


def _write_executable(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


POSIX_TOOL = r'''#!/bin/sh
set -eu
printf '%s|%s|%s\n' "$(basename "$0")" "$PWD" "$*" >> "$FAKE_LOG"

case "${1:-}" in
    -c)
        [ "$(basename "$0")" = "${FAKE_SUPPORTED:-python3}" ]
        ;;
    scripts/bootstrap.py)
        exit "${FAKE_BOOTSTRAP_STATUS:-0}"
        ;;
    scripts/doctor.py)
        exit "${FAKE_DOCTOR_STATUS:-0}"
        ;;
esac
'''


def _posix_fixture(tmp_path, *, supported="python3", bootstrap=0, doctor=0, poetry=True):
    repo = tmp_path / "repo with spaces"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_SH, repo / "install.sh")
    (repo / "scripts" / "bootstrap.py").write_text("", encoding="utf-8")
    (repo / "scripts" / "doctor.py").write_text("", encoding="utf-8")

    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("python3", "python"):
        _write_executable(tools / name, POSIX_TOOL)
    if poetry:
        _write_executable(
            tools / "poetry",
            "#!/bin/sh\nprintf '%s|%s|%s\\n' "
            '"$(basename "$0")" "$PWD" "$*" >> "$FAKE_LOG"\n'
            "exit \"${FAKE_POETRY_STATUS:-0}\"\n",
        )

    log = tmp_path / "calls.log"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tools}{os.pathsep}{env.get('PATH', '')}",
            "FAKE_LOG": str(log),
            "FAKE_SUPPORTED": supported,
            "FAKE_BOOTSTRAP_STATUS": str(bootstrap),
            "FAKE_DOCTOR_STATUS": str(doctor),
        }
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    return repo, log, env, outside


def _run_posix(repo, env, outside):
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(
        [bash, str(repo / "install.sh")],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
    )


def _phase_calls(log, repo):
    return [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if f"|{repo}|scripts/" in line
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_install_sh_resolves_root_runs_phases_and_preserves_state(tmp_path):
    repo, log, env, outside = _posix_fixture(tmp_path)
    config = repo / "config.conf"
    env_file = repo / ".env"
    runtime = repo / "runtime"
    config.write_text("keep config\n", encoding="utf-8")
    env_file.write_text("keep env\n", encoding="utf-8")
    (runtime / "auth").mkdir(parents=True)
    state = runtime / "auth" / "gemini.json"
    state.write_text("keep state\n", encoding="utf-8")

    result = _run_posix(repo, env, outside)
    rerun = _run_posix(repo, env, outside)

    assert result.returncode == 0
    assert rerun.returncode == 0
    assert _phase_calls(log, repo) == [
        f"python3|{repo}|scripts/bootstrap.py",
        f"python3|{repo}|scripts/doctor.py",
    ] * 2
    assert config.read_text(encoding="utf-8") == "keep config\n"
    assert env_file.read_text(encoding="utf-8") == "keep env\n"
    assert state.read_text(encoding="utf-8") == "keep state\n"
    assert "verify_login.py" in result.stdout
    assert "src/run.py" in result.stdout
    assert "http://localhost:6969/ui" in result.stdout
    assert "Setup complete." in result.stdout
    assert "verify_login.py" not in log.read_text(encoding="utf-8")
    assert "src/run.py" not in log.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_install_sh_bootstrap_failure_stops_before_doctor(tmp_path):
    repo, log, env, outside = _posix_fixture(tmp_path, bootstrap=17)

    result = _run_posix(repo, env, outside)

    assert result.returncode == 17
    assert _phase_calls(log, repo) == [f"python3|{repo}|scripts/bootstrap.py"]
    assert "Setup complete." not in result.stdout
    assert "bootstrap failed" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_install_sh_doctor_failure_stops_and_propagates_status(tmp_path):
    repo, log, env, outside = _posix_fixture(tmp_path, doctor=23)

    result = _run_posix(repo, env, outside)

    assert result.returncode == 23
    assert _phase_calls(log, repo) == [
        f"python3|{repo}|scripts/bootstrap.py",
        f"python3|{repo}|scripts/doctor.py",
    ]
    assert "doctor failed" in result.stderr
    assert "Setup complete." not in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_install_sh_falls_back_from_python3_to_python(tmp_path):
    repo, log, env, outside = _posix_fixture(tmp_path, supported="python")

    result = _run_posix(repo, env, outside)

    assert result.returncode == 0
    assert _phase_calls(log, repo) == [
        f"python|{repo}|scripts/bootstrap.py",
        f"python|{repo}|scripts/doctor.py",
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_install_sh_rejects_unsupported_python_before_poetry(tmp_path):
    repo, log, env, outside = _posix_fixture(tmp_path, supported="none")

    result = _run_posix(repo, env, outside)

    assert result.returncode == 1
    assert "no supported interpreter" in result.stderr
    assert "poetry|" not in log.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_install_sh_rejects_missing_poetry(tmp_path):
    repo, log, env, outside = _posix_fixture(tmp_path, poetry=False)
    tools = tmp_path / "tools"
    for utility in ("basename", "dirname"):
        utility_path = shutil.which(utility)
        assert utility_path is not None
        (tools / utility).symlink_to(utility_path)
    env["PATH"] = str(tools)

    result = _run_posix(repo, env, outside)

    assert result.returncode == 1
    assert "Poetry was not found on PATH" in result.stderr
    assert not log.exists() or "scripts/" not in log.read_text(encoding="utf-8")


def test_install_ps1_exposes_root_and_required_contract():
    content = INSTALL_PS1.read_text(encoding="utf-8")

    assert "$PSScriptRoot" in content
    assert "Set-Location -LiteralPath" in content
    assert 'foreach ($version in @("3.12", "3.11"))' in content
    assert "Get-Command python" in content
    assert "Get-Command poetry" in content
    assert '"scripts\\bootstrap.py"' in content
    assert '"scripts\\doctor.py"' in content
    assert "exit $status" in content
    assert "git clone" not in content.lower()
    assert "Start-Process" not in content


def _powershell_executable():
    for name in ("pwsh", "powershell"):
        executable = shutil.which(name)
        if executable:
            return executable
    return None


POWERSHELL_POSIX_TOOL = r'''#!/bin/sh
set -eu
name=$(basename "$0")
printf '%s|%s|%s\n' "$name" "$PWD" "$*" >> "$FAKE_LOG"

if [ "$name" = "py" ]; then
    case "${1:-}:${2:-}" in
        -3.12:-c) [ "${FAKE_PY312_OK:-0}" = "1" ] ;;
        -3.11:-c) [ "${FAKE_PY311_OK:-0}" = "1" ] ;;
        -3.12:scripts\\bootstrap.py|-3.12:scripts/bootstrap.py)
            [ "${FAKE_PY312_OK:-0}" = "1" ] && exit "${FAKE_BOOTSTRAP_STATUS:-0}" ;;
        -3.12:scripts\\doctor.py|-3.12:scripts/doctor.py)
            [ "${FAKE_PY312_OK:-0}" = "1" ] && exit "${FAKE_DOCTOR_STATUS:-0}" ;;
        -3.11:scripts\\bootstrap.py|-3.11:scripts/bootstrap.py)
            [ "${FAKE_PY311_OK:-0}" = "1" ] && exit "${FAKE_BOOTSTRAP_STATUS:-0}" ;;
        -3.11:scripts\\doctor.py|-3.11:scripts/doctor.py)
            [ "${FAKE_PY311_OK:-0}" = "1" ] && exit "${FAKE_DOCTOR_STATUS:-0}" ;;
    esac
    exit 1
fi

if [ "$name" = "python" ]; then
    case "${1:-}" in
        -c) [ "${FAKE_PYTHON_OK:-0}" = "1" ] ;;
        scripts\\bootstrap.py|scripts/bootstrap.py) exit "${FAKE_BOOTSTRAP_STATUS:-0}" ;;
        scripts\\doctor.py|scripts/doctor.py) exit "${FAKE_DOCTOR_STATUS:-0}" ;;
    esac
fi
'''


POWERSHELL_CMD_TOOL = r'''@echo off
>>"%FAKE_LOG%" echo %~nx0^|%CD%^|%*
if /I "%~nx0"=="py.cmd" goto py
if /I "%~nx0"=="python.cmd" goto python
if /I "%~nx0"=="poetry.cmd" goto poetry
exit /b 0
:py
if "%1"=="-3.12" if "%2"=="-c" if "%FAKE_PY312_OK%"=="1" exit /b 0
if "%1"=="-3.11" if "%2"=="-c" if "%FAKE_PY311_OK%"=="1" exit /b 0
if "%1"=="-3.12" if "%2"=="scripts\bootstrap.py" if "%FAKE_PY312_OK%"=="1" exit /b %FAKE_BOOTSTRAP_STATUS%
if "%1"=="-3.12" if "%2"=="scripts\doctor.py" if "%FAKE_PY312_OK%"=="1" exit /b %FAKE_DOCTOR_STATUS%
if "%1"=="-3.11" if "%2"=="scripts\bootstrap.py" if "%FAKE_PY311_OK%"=="1" exit /b %FAKE_BOOTSTRAP_STATUS%
if "%1"=="-3.11" if "%2"=="scripts\doctor.py" if "%FAKE_PY311_OK%"=="1" exit /b %FAKE_DOCTOR_STATUS%
exit /b 1
:python
if "%1"=="-c" if "%FAKE_PYTHON_OK%"=="1" exit /b 0
if "%1"=="scripts\bootstrap.py" exit /b %FAKE_BOOTSTRAP_STATUS%
if "%1"=="scripts\doctor.py" exit /b %FAKE_DOCTOR_STATUS%
exit /b 0
:poetry
if "%1"=="--version" exit /b %FAKE_POETRY_STATUS%
exit /b 0
'''


def _powershell_fixture(tmp_path, *, py312=False, py311=False, python=False, bootstrap=0, doctor=0):
    repo = tmp_path / "repo with spaces"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALL_PS1, repo / "install.ps1")

    tools = tmp_path / "powershell tools"
    tools.mkdir()
    if os.name == "nt":
        tool_content = POWERSHELL_CMD_TOOL
        suffix = ".cmd"
    else:
        tool_content = POWERSHELL_POSIX_TOOL
        suffix = ""
    for name in ("py", "python", "poetry"):
        _write_executable(tools / f"{name}{suffix}", tool_content)

    log = tmp_path / "calls.log"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tools}{os.pathsep}{env.get('PATH', '')}",
            "FAKE_LOG": str(log),
            "FAKE_PY312_OK": "1" if py312 else "0",
            "FAKE_PY311_OK": "1" if py311 else "0",
            "FAKE_PYTHON_OK": "1" if python else "0",
            "FAKE_BOOTSTRAP_STATUS": str(bootstrap),
            "FAKE_DOCTOR_STATUS": str(doctor),
            "FAKE_POETRY_STATUS": "0",
        }
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    return repo, log, env, outside


@pytest.mark.skipif(_powershell_executable() is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize(
    ("py312", "py311", "python", "expected"),
    [
        (True, False, False, "py"),
        (False, True, False, "py"),
        (False, False, True, "python"),
    ],
)
def test_install_ps1_python_resolution_and_root(tmp_path, py312, py311, python, expected):
    repo, log, env, outside = _powershell_fixture(
        tmp_path, py312=py312, py311=py311, python=python
    )
    powershell = _powershell_executable()

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(repo / "install.ps1")],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    phase_calls = [
        line for line in log.read_text(encoding="utf-8").splitlines()
        if f"|{repo}|scripts\\" in line or f"|{repo}|scripts/" in line
    ]
    assert len(phase_calls) == 2
    assert all(line.startswith(f"{expected}|{repo}|") for line in phase_calls)
    if expected == "py":
        selected_version = "-3.12" if py312 else "-3.11"
        assert all(selected_version in line for line in phase_calls)
    assert "Setup complete." in result.stdout


@pytest.mark.skipif(_powershell_executable() is None, reason="PowerShell is unavailable")
def test_install_ps1_propagates_phase_failure(tmp_path):
    repo, log, env, outside = _powershell_fixture(tmp_path, py312=True, bootstrap=19)
    powershell = _powershell_executable()

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(repo / "install.ps1")],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 19
    assert "bootstrap failed" in result.stderr
    assert "Setup complete." not in result.stdout
