[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
Set-Location -LiteralPath $repoRoot

$pythonProbe = @'
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd() / "src"))
from app.utils.python_version import classify_python_version

result = classify_python_version(sys.version_info, platform_name="nt")
print(json.dumps(result, separators=(",", ":")))
raise SystemExit(0 if result["supported"] else 1)
'@
$pythonExecutable = $null
$pythonArguments = @()
$pythonCandidates = @()
$candidateDiagnostics = @()

$py = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $py) {
    foreach ($version in @("3.12", "3.11")) {
        $pythonCandidates += @{
            Label = "py -$version"
            Executable = $py.Source
            Arguments = @("-$version")
        }
    }
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $python) {
    $pythonCandidates += @{
        Label = "python"
        Executable = $python.Source
        Arguments = @()
    }
}

foreach ($candidate in $pythonCandidates) {
    $candidateArguments = @($candidate.Arguments)
    try {
        $probeOutput = @($pythonProbe | & $candidate.Executable @candidateArguments "-" 2>&1)
    }
    catch {
        $errorText = [string]$_.Exception.Message
        if ([string]::IsNullOrWhiteSpace($errorText)) {
            $errorText = "native command failed"
        }
        $errorText = ($errorText -replace "\s+", " ").Trim()
        if ($errorText.Length -gt 200) {
            $errorText = $errorText.Substring(0, 197) + "..."
        }
        $candidateDiagnostics += "$($candidate.Label) -> probe execution failed: $errorText"
        continue
    }
    $probeStatus = $LASTEXITCODE
    $probeText = (($probeOutput | ForEach-Object { "$_" }) -join "`n").Trim()
    $probe = $null

    if (-not [string]::IsNullOrWhiteSpace($probeText)) {
        try {
            $probe = $probeText | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            $probe = $null
        }
    }

    if ($null -ne $probe -and [bool]$probe.supported -and $probeStatus -eq 0) {
        $pythonExecutable = $candidate.Executable
        $pythonArguments = @($candidate.Arguments)
        break
    }

    if ($null -ne $probe) {
        $version = [string]$probe.version
        if ([string]::IsNullOrWhiteSpace($version)) {
            $version = "unknown"
        }
        $required = [string]$probe.required
        if ([string]::IsNullOrWhiteSpace($required)) {
            $required = [string]$probe.supported_range
        }
        $reasonText = switch ([string]$probe.reason) {
            "windows_patch_too_old" { "Windows requires Python $required" }
            "unsupported_major_minor" { "supported range is $required" }
            default { "reason: $([string]$probe.reason)" }
        }
        $candidateDiagnostics += "$($candidate.Label) -> Python $($version): rejected; $reasonText"
    }
    elseif ($probeStatus -eq 0) {
        $candidateDiagnostics += "$($candidate.Label) -> probe returned invalid JSON"
    }
    elseif (-not [string]::IsNullOrWhiteSpace($probeText)) {
        $errorText = ($probeText -replace "\s+", " ").Trim()
        if ($errorText.Length -gt 200) {
            $errorText = $errorText.Substring(0, 197) + "..."
        }
        $candidateDiagnostics += "$($candidate.Label) -> probe execution failed: $errorText"
    }
    else {
        $candidateDiagnostics += "$($candidate.Label) -> no usable Python version (exit code $probeStatus)"
    }
}

if ($null -eq $pythonExecutable) {
    [Console]::Error.WriteLine("No supported Python interpreter was found.")
    foreach ($diagnostic in $candidateDiagnostics) {
        [Console]::Error.WriteLine("  $diagnostic")
    }
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
