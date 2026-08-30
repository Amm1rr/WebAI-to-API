import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"
INSTALLATION = ROOT / "docs" / "installation.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"


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


PHASE_SCRIPTS = (
    "scripts\\bootstrap.py",
    "scripts/bootstrap.py",
    "scripts\\doctor.py",
    "scripts/doctor.py",
)


def _parse_tool_call(line):
    return line.split("|", 2)


def _is_phase_call(line, repo):
    _, cwd, args = _parse_tool_call(line)
    return cwd == str(repo) and any(script in args for script in PHASE_SCRIPTS)


def _normalized_tool_name(name):
    return name[:-4] if name.lower().endswith(".cmd") else name


def _phase_calls(log, repo):
    return [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if _is_phase_call(line, repo)
    ]


def _powershell_phase_calls(log, repo):
    return [
        (_normalized_tool_name(name), args)
        for line in log.read_text(encoding="utf-8").splitlines()
        if _is_phase_call(line, repo)
        for name, _, args in [_parse_tool_call(line)]
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
    assert "app.utils.python_version" in content
    assert "classify_python_version" in content
    assert "json.dumps" in content
    assert "ConvertFrom-Json" in content
    assert "candidateDiagnostics" in content
    assert "rejected;" in content
    assert '$pythonProbe | &' in content
    assert ' @candidateArguments "-"' in content
    assert '"-c"' not in content
    assert 'platform_name="nt"' in content
    assert 'foreach ($version in @("3.12", "3.11"))' in content
    assert "Get-Command python" in content
    assert "Get-Command poetry" in content
    assert '"scripts\\bootstrap.py"' in content
    assert '"scripts\\doctor.py"' in content
    assert "3.11.10" not in content
    assert "3.12.4" not in content
    assert "exit $status" in content
    assert "git clone" not in content.lower()
    assert "Start-Process" not in content


def test_install_ps1_delimits_version_interpolation():
    content = INSTALL_PS1.read_text(encoding="utf-8")

    assert '$($candidate.Label) -> Python $($version): rejected; $reasonText' in content
    assert '$($candidate.Label) -> Python $version: rejected; $reasonText' not in content


def test_installation_documents_windows_setup_recovery_without_persistent_policy_change():
    content = INSTALLATION.read_text(encoding="utf-8")

    assert "Windows PowerShell" in content
    assert ".\\install.ps1" in content
    assert "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass" in content
    assert "current PowerShell session only" in content
    assert "user or machine execution policy" in content
    assert "Set-ExecutionPolicy -Scope CurrentUser" not in content
    assert "Set-ExecutionPolicy -Scope LocalMachine" not in content
    assert "%APPDATA%\\Python\\Scripts" in content
    assert "reopen PowerShell" in content
    assert "python scripts/bootstrap.py" in content
    assert "poetry run python scripts/doctor.py" in content


def test_configuration_documents_shared_auth_path_as_configurable():
    content = CONFIGURATION.read_text(encoding="utf-8")

    assert "runtime/auth/gemini.json" in content
    assert "same persisted Google" in content
    assert "authentication cookies" in content
    assert "both Playwright and Gemini WebAPI" in content
    assert "authentication state only" in content
    assert "does not share browser processes" in content


def _powershell_executable():
    for name in ("pwsh", "powershell"):
        executable = shutil.which(name)
        if executable:
            return executable
    return None


@pytest.mark.skipif(_powershell_executable() is None, reason="PowerShell is unavailable")
def test_install_ps1_parses_without_errors():
    powershell = _powershell_executable()
    env = os.environ.copy()
    env["INSTALL_PS1_PATH"] = str(INSTALL_PS1)
    parser_script = r'''
$path = [Environment]::GetEnvironmentVariable("INSTALL_PS1_PATH")
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -ne 0) {
    $errors | ForEach-Object { Write-Error $_.Message }
    exit 1
}
exit 0
'''

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", parser_script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout


POWERSHELL_POSIX_TOOL = r'''#!/bin/sh
set -eu
name=$(basename "$0")
printf '%s|%s|%s\n' "$name" "$PWD" "$*" >> "$FAKE_LOG"

probe_version() {
    case "${1:-}" in
        3.11.10|3.11.1[1-9]|3.11.[2-9][0-9]|3.11.[1-9][0-9][0-9]*)
            printf '{"supported":true,"version":"%s","reason":"supported","supported_range":">=3.11,<3.13","required":"3.11.10+"}\n' "$1"
            return 0
            ;;
        3.12.[4-9]|3.12.[1-9][0-9]*)
            printf '{"supported":true,"version":"%s","reason":"supported","supported_range":">=3.11,<3.13","required":"3.12.4+"}\n' "$1"
            return 0
            ;;
        3.11.*)
            printf '{"supported":false,"version":"%s","reason":"windows_patch_too_old","supported_range":">=3.11,<3.13","required":"3.11.10+"}\n' "$1"
            return 1
            ;;
        3.12.*)
            printf '{"supported":false,"version":"%s","reason":"windows_patch_too_old","supported_range":">=3.11,<3.13","required":"3.12.4+"}\n' "$1"
            return 1
            ;;
        3.*)
            printf '{"supported":false,"version":"%s","reason":"unsupported_major_minor","supported_range":">=3.11,<3.13","required":">=3.11,<3.13"}\n' "$1"
            return 1
            ;;
    esac
    return 1
}

secure_version() {
    probe_version "$1" >/dev/null 2>&1
    return $?
}

if [ "$name" = "py" ]; then
    case "${1:-}:${2:-}" in
        -3.12:-)
            if probe_version "${FAKE_PY312_VERSION:-}"; then exit 0; fi
            exit 1
            ;;
        -3.11:-)
            if probe_version "${FAKE_PY311_VERSION:-}"; then exit 0; fi
            exit 1
            ;;
        -3.12:scripts\\bootstrap.py|-3.12:scripts/bootstrap.py)
            if secure_version "${FAKE_PY312_VERSION:-}"; then exit "${FAKE_BOOTSTRAP_STATUS:-0}"; fi
            exit 1
            ;;
        -3.12:scripts\\doctor.py|-3.12:scripts/doctor.py)
            if secure_version "${FAKE_PY312_VERSION:-}"; then exit "${FAKE_DOCTOR_STATUS:-0}"; fi
            exit 1
            ;;
        -3.11:scripts\\bootstrap.py|-3.11:scripts/bootstrap.py)
            if secure_version "${FAKE_PY311_VERSION:-}"; then exit "${FAKE_BOOTSTRAP_STATUS:-0}"; fi
            exit 1
            ;;
        -3.11:scripts\\doctor.py|-3.11:scripts/doctor.py)
            if secure_version "${FAKE_PY311_VERSION:-}"; then exit "${FAKE_DOCTOR_STATUS:-0}"; fi
            exit 1
            ;;
    esac
    exit 1
fi

if [ "$name" = "python" ]; then
    case "${1:-}" in
        -)
            if [ "${FAKE_PYTHON_PROBE_FAILURE:-}" = "1" ]; then
                printf '%s\n' "Python was not found; run without arguments to install from the Microsoft Store..." >&2
                exit 1
            fi
            if [ "${FAKE_PYTHON_INVALID_OUTPUT:-}" = "1" ]; then
                printf '%s\n' "not JSON"
                exit 0
            fi
            if probe_version "${FAKE_PYTHON_VERSION:-}"; then exit 0; fi
            exit 1
            ;;
        scripts\\bootstrap.py|scripts/bootstrap.py)
            if secure_version "${FAKE_PYTHON_VERSION:-}"; then exit "${FAKE_BOOTSTRAP_STATUS:-0}"; fi
            exit 1
            ;;
        scripts\\doctor.py|scripts/doctor.py)
            if secure_version "${FAKE_PYTHON_VERSION:-}"; then exit "${FAKE_DOCTOR_STATUS:-0}"; fi
            exit 1
            ;;
    esac
fi

if [ "$name" = "poetry" ]; then
    if [ "${1:-}" = "--version" ]; then
        exit "${FAKE_POETRY_STATUS:-0}"
    fi
    exit 0
fi
exit 1
'''


POWERSHELL_CMD_TOOL = r'''@echo off
>>"%FAKE_LOG%" echo %~nx0^|%CD%^|%*
if /I "%~nx0"=="py.cmd" goto py
if /I "%~nx0"=="python.cmd" goto python
if /I "%~nx0"=="poetry.cmd" goto poetry
exit /b 0
:py
if "%1"=="-3.12" if "%2"=="-" (
    call :probe "%FAKE_PY312_VERSION%"
    if not errorlevel 1 exit /b 0
)
if "%1"=="-3.11" if "%2"=="-" (
    call :probe "%FAKE_PY311_VERSION%"
    if not errorlevel 1 exit /b 0
)
if "%1"=="-3.12" if "%2"=="scripts\bootstrap.py" (
    call :secure "%FAKE_PY312_VERSION%"
    if not errorlevel 1 exit /b %FAKE_BOOTSTRAP_STATUS%
)
if "%1"=="-3.12" if "%2"=="scripts\doctor.py" (
    call :secure "%FAKE_PY312_VERSION%"
    if not errorlevel 1 exit /b %FAKE_DOCTOR_STATUS%
)
if "%1"=="-3.11" if "%2"=="scripts\bootstrap.py" (
    call :secure "%FAKE_PY311_VERSION%"
    if not errorlevel 1 exit /b %FAKE_BOOTSTRAP_STATUS%
)
if "%1"=="-3.11" if "%2"=="scripts\doctor.py" (
    call :secure "%FAKE_PY311_VERSION%"
    if not errorlevel 1 exit /b %FAKE_DOCTOR_STATUS%
)
exit /b 1
:python
if "%1"=="-" (
    if "%FAKE_PYTHON_PROBE_FAILURE%"=="1" (
        echo Python was not found; run without arguments to install from the Microsoft Store... 1>&2
        exit /b 1
    )
    if "%FAKE_PYTHON_INVALID_OUTPUT%"=="1" (
        echo not JSON
        exit /b 0
    )
    call :probe "%FAKE_PYTHON_VERSION%"
    if not errorlevel 1 exit /b 0
)
if "%1"=="scripts\bootstrap.py" (
    call :secure "%FAKE_PYTHON_VERSION%"
    if not errorlevel 1 exit /b %FAKE_BOOTSTRAP_STATUS%
)
if "%1"=="scripts\doctor.py" (
    call :secure "%FAKE_PYTHON_VERSION%"
    if not errorlevel 1 exit /b %FAKE_DOCTOR_STATUS%
)
exit /b 1
:poetry
if "%1"=="--version" exit /b %FAKE_POETRY_STATUS%
exit /b 0
:probe
if "%~1"=="" exit /b 1
for /f "tokens=1-3 delims=." %%A in ("%~1") do (
    if "%%A.%%B"=="3.11" (
        if %%C GEQ 10 (
            echo {"supported":true,"version":"%~1","reason":"supported","supported_range":">=3.11,<3.13","required":"3.11.10+"}
            exit /b 0
        )
        echo {"supported":false,"version":"%~1","reason":"windows_patch_too_old","supported_range":">=3.11,<3.13","required":"3.11.10+"}
        exit /b 1
    )
    if "%%A.%%B"=="3.12" (
        if %%C GEQ 4 (
            echo {"supported":true,"version":"%~1","reason":"supported","supported_range":">=3.11,<3.13","required":"3.12.4+"}
            exit /b 0
        )
        echo {"supported":false,"version":"%~1","reason":"windows_patch_too_old","supported_range":">=3.11,<3.13","required":"3.12.4+"}
        exit /b 1
    )
)
echo {"supported":false,"version":"%~1","reason":"unsupported_major_minor","supported_range":">=3.11,<3.13","required":">=3.11,<3.13"}
exit /b 1
:secure
for /f "tokens=1-3 delims=." %%A in ("%~1") do (
    if "%%A.%%B"=="3.11" if %%C GEQ 10 exit /b 0
    if "%%A.%%B"=="3.12" if %%C GEQ 4 exit /b 0
)
exit /b 1
'''


def _powershell_fixture(
    tmp_path,
    *,
    py312=False,
    py311=False,
    python=False,
    py312_version=None,
    py311_version=None,
    python_version=None,
    python_probe_failure=False,
    python_invalid_output=False,
    bootstrap=0,
    doctor=0,
):
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
            "FAKE_PY312_VERSION": py312_version or ("3.12.4" if py312 else ""),
            "FAKE_PY311_VERSION": py311_version or ("3.11.10" if py311 else ""),
            "FAKE_PYTHON_VERSION": python_version or ("3.12.4" if python else ""),
            "FAKE_PYTHON_PROBE_FAILURE": "1" if python_probe_failure else "0",
            "FAKE_PYTHON_INVALID_OUTPUT": "1" if python_invalid_output else "0",
            "FAKE_BOOTSTRAP_STATUS": str(bootstrap),
            "FAKE_DOCTOR_STATUS": str(doctor),
            "FAKE_POETRY_STATUS": "0",
        }
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    return repo, log, env, outside


@pytest.mark.skipif(_powershell_executable() is None, reason="PowerShell is unavailable")
def test_install_ps1_continues_after_python_probe_execution_failure(tmp_path):
    repo, log, env, outside = _powershell_fixture(
        tmp_path,
        python_probe_failure=True,
    )
    powershell = _powershell_executable()

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(repo / "install.ps1")],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "No supported Python interpreter was found." in result.stderr
    assert "python -> probe execution failed:" in result.stderr
    assert "Python was not found" in result.stderr
    assert "Setup complete." not in result.stdout
    phase_calls = _powershell_phase_calls(log, repo)
    assert phase_calls == []


@pytest.mark.skipif(_powershell_executable() is None, reason="PowerShell is unavailable")
def test_install_ps1_reports_invalid_python_probe_output(tmp_path):
    repo, log, env, outside = _powershell_fixture(tmp_path, python_invalid_output=True)
    powershell = _powershell_executable()

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(repo / "install.ps1")],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "python -> probe returned invalid JSON" in result.stderr
    assert "probe execution failed" not in result.stderr


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
    phase_calls = _powershell_phase_calls(log, repo)
    assert len(phase_calls) == 2
    assert all(name == expected for name, _ in phase_calls)
    if expected == "py":
        selected_version = "-3.12" if py312 else "-3.11"
        assert all(selected_version in args for _, args in phase_calls)
    assert any("bootstrap.py" in args for _, args in phase_calls)
    assert any("doctor.py" in args for _, args in phase_calls)
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


@pytest.mark.skipif(_powershell_executable() is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize(
    (
        "py312_version",
        "py311_version",
        "python_version",
        "expected_status",
        "expected_runner",
        "expected_detail",
    ),
    [
        ("3.12.3", "3.11.10", None, 0, "-3.11", None),
        ("3.12.4", "3.11.9", None, 0, "-3.12", None),
        ("3.11.9", None, None, 1, None, "Windows requires Python 3.11.10+"),
        (None, None, "3.12.3", 1, None, "Windows requires Python 3.12.4+"),
        (None, None, "3.13.1", 1, None, "supported range is >=3.11,<3.13"),
        (None, None, "3.10.9", 1, None, "supported range is >=3.11,<3.13"),
        (None, None, "3.12.4", 0, "python", None),
    ],
)
def test_install_ps1_enforces_windows_python_patch_contract(
    tmp_path,
    py312_version,
    py311_version,
    python_version,
    expected_status,
    expected_runner,
    expected_detail,
):
    repo, log, env, outside = _powershell_fixture(
        tmp_path,
        py312_version=py312_version,
        py311_version=py311_version,
        python_version=python_version,
    )
    powershell = _powershell_executable()

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(repo / "install.ps1")],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == expected_status
    if expected_runner is None:
        assert expected_detail in result.stderr
    else:
        phase_calls = _powershell_phase_calls(log, repo)
        assert len(phase_calls) == 2
        if expected_runner == "python":
            assert all(name == "python" for name, _ in phase_calls)
        else:
            assert all(name == "py" for name, _ in phase_calls)
            assert all(expected_runner in args for _, args in phase_calls)
        assert any("bootstrap.py" in args for _, args in phase_calls)
        assert any("doctor.py" in args for _, args in phase_calls)
