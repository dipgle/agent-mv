# Supervisor weekly cron — Windows equivalent of weekly.sh.

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectDir

if (Test-Path "venv\Scripts\Activate.ps1") {
    & "venv\Scripts\Activate.ps1"
}

Write-Host "=== $(Get-Date) — weekly cron start ==="
python orchestrator\supervisor\scan.py
python orchestrator\supervisor\propose.py
python orchestrator\supervisor\auto_promote.py
Write-Host "=== $(Get-Date) — weekly cron done ==="
