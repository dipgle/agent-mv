# Supervisor daily cron — Windows equivalent of daily.sh.
# Schedule via Task Scheduler:
#   New Task -> Daily 02:00 -> Action: powershell -File orchestrator\cron\daily.ps1

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectDir

if (Test-Path "venv\Scripts\Activate.ps1") {
    & "venv\Scripts\Activate.ps1"
}

Write-Host "=== $(Get-Date) — daily cron start ==="
python orchestrator\supervisor\audit.py
python orchestrator\supervisor\cost_rollup.py
python orchestrator\supervisor\fetch_outcomes.py
python orchestrator\supervisor\auto_promote.py
Write-Host "=== $(Get-Date) — daily cron done ==="
