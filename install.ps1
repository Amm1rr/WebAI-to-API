[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
Set-Location -LiteralPath $repoRoot

$pythonProbe = @'
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd() / "src"))
from app.utils.python_version import is_supported_python

raise SystemExit(
    0 if is_supported_python(sys.version_info, platform_name="nt") else 1
)
'@
$pythonExecutable = $null
$pythonArguments = @()

$py = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $py) {
    foreach ($version in @("3.12", "3.11")) {
        & $py.Source "-$version" "-c" $pythonProbe *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonExecutable = $py.Source
            $pythonArguments = @("-$version")
            break
        }
    }
}

if ($null -eq $pythonExecutable) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        & $python.Source "-c" $pythonProbe *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonExecutable = $python.Source
        }
    }
}

if ($null -eq $pythonExecutable) {
    [Console]::Error.WriteLine("Secure Python required: Windows Python 3.11.10+ or 3.12.4+ is required for the Gemini WebAPI private cookie cache. No secure supported interpreter was found. Upgrade Python, then rerun this script.")
    exit 1
}

$poetry = Get-Command poetry -ErrorAction SilentlyContinue
if ($null -eq $poetry) {
    [Console]::Error.WriteLine("Poetry was not found on PATH. Install Poetry from https://python-poetry.org/docs/#installation, reopen PowerShell, then rerun this script.")
    exit 1
}

& $poetry.Source "--version" *> $null
$poetryStatus = $LASTEXITCODE
if ($poetryStatus -ne 0) {
    [Console]::Error.WriteLine("Poetry is on PATH but could not execute. Check the Poetry installation and rerun this script.")
    exit $poetryStatus
}

function Invoke-RequiredPhase {
    param(
        [Parameter(Mandatory = $true)] [string] $Name,
        [Parameter(Mandatory = $true)] [string] $ScriptPath
    )

    Write-Host "==> $Name"
    & $pythonExecutable @pythonArguments $ScriptPath
    $status = $LASTEXITCODE
    if ($status -ne 0) {
        [Console]::Error.WriteLine("$Name failed (exit code $status).")
        exit $status
    }
}

Invoke-RequiredPhase -Name "bootstrap" -ScriptPath "scripts\bootstrap.py"
Invoke-RequiredPhase -Name "doctor" -ScriptPath "scripts\doctor.py"

Write-Host ""
Write-Host "Setup complete."
Write-Host "Next steps:"
Write-Host "  Authentication (when needed): poetry run python verify_login.py"
Write-Host "  Start server:                 poetry run python src/run.py"
Write-Host "  Dashboard:                    http://localhost:6969/ui"
exit 0
