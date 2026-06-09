# Convenience entry point for Windows.
#   .\run.ps1 setup            -> run infra\setup.ps1
#   .\run.ps1 check            -> scripts\check_env.py
#   .\run.ps1 pipeline ARGS    -> orchestrator\pipeline.py ARGS
#   .\run.ps1 audit            -> orchestrator\supervisor\audit.py
#   .\run.ps1 cost             -> orchestrator\supervisor\cost_rollup.py
#   .\run.ps1 scan             -> orchestrator\supervisor\scan.py
#   .\run.ps1 propose          -> orchestrator\supervisor\propose.py
#   .\run.ps1 promote          -> orchestrator\supervisor\auto_promote.py
#   .\run.ps1 cron-daily       -> orchestrator\cron\daily.ps1
#   .\run.ps1 cron-weekly      -> orchestrator\cron\weekly.ps1
#   .\run.ps1 dashboard        -> open eval\dashboard.html

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# UTF-8 console (Windows default is cp1252, breaks emoji / Vietnamese)
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null

if (Test-Path "venv\Scripts\Activate.ps1") {
    & "venv\Scripts\Activate.ps1"
}

$cmd, $rest = $args

switch ($cmd) {
    "setup"       { & ".\infra\setup.ps1"; break }
    "check"       { python scripts\check_env.py @rest; break }
    "pipeline"    { python orchestrator\pipeline.py @rest; break }
    "audit"       { python orchestrator\supervisor\audit.py @rest; break }
    "cost"        { python orchestrator\supervisor\cost_rollup.py @rest; break }
    "scan"        { python orchestrator\supervisor\scan.py @rest; break }
    "propose"     { python orchestrator\supervisor\propose.py @rest; break }
    "promote"     { python orchestrator\supervisor\auto_promote.py @rest; break }
    "cron-daily"  { & ".\orchestrator\cron\daily.ps1"; break }
    "cron-weekly" { & ".\orchestrator\cron\weekly.ps1"; break }
    "dashboard"   { python eval\serve.py @rest; break }
    default {
        Write-Host "Usage: .\run.ps1 <command> [args]"
        Write-Host ""
        Write-Host "Commands:"
        Write-Host "  setup        Install everything (first run)"
        Write-Host "  check        Verify environment"
        Write-Host "  pipeline     Render a video"
        Write-Host "  audit        System audit (bottleneck/waste/...)"
        Write-Host "  cost         Cost roll-up"
        Write-Host "  scan         External scan"
        Write-Host "  propose      Generate improvement proposals"
        Write-Host "  promote      Run auto-promotion canaries"
        Write-Host "  cron-daily   Run daily cron now"
        Write-Host "  cron-weekly  Run weekly cron now"
        Write-Host "  dashboard    Open eval dashboard"
    }
}
