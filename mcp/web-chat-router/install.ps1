# install.ps1 — Windows setup for web-chat-router MCP server.
# Run once after cloning. Safe to re-run (idempotent).
# Requires PowerShell 5.1+ and Node.js >= 20.
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "[web-chat-router] Checking Node.js version..." -ForegroundColor Cyan
$nodeVersion = & node --version 2>$null
if (-not $nodeVersion) {
    Write-Host "ERROR: Node.js not found. Install from https://nodejs.org/ (v20+)" -ForegroundColor Red
    exit 1
}
$nodeMajor = [int]($nodeVersion -replace 'v(\d+).*', '$1')
if ($nodeMajor -lt 20) {
    Write-Host "ERROR: Node.js >= 20 required. Got: $nodeVersion" -ForegroundColor Red
    Write-Host "Install from https://nodejs.org/ or via: winget install OpenJS.NodeJS.LTS" -ForegroundColor Yellow
    exit 1
}
Write-Host "  Node.js $nodeVersion OK" -ForegroundColor Green

Write-Host "[web-chat-router] Installing npm dependencies..." -ForegroundColor Cyan
& npm install
if ($LASTEXITCODE -ne 0) { Write-Host "npm install failed" -ForegroundColor Red; exit 1 }

Write-Host "[web-chat-router] Installing Playwright Chromium browser..." -ForegroundColor Cyan
& npx playwright install chromium --with-deps
if ($LASTEXITCODE -ne 0) {
    Write-Host "Playwright install failed. You may need to run as Administrator." -ForegroundColor Red
    exit 1
}

Write-Host "[web-chat-router] Building TypeScript..." -ForegroundColor Cyan
& npm run build
if ($LASTEXITCODE -ne 0) { Write-Host "TypeScript build failed" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "[web-chat-router] Install complete." -ForegroundColor Green
Write-Host ""
Write-Host "To add to your .mcp.json, see: ..\.mcp.json.template" -ForegroundColor Yellow
Write-Host ""
Write-Host "Manual test:"
Write-Host "  npx tsx src\server.ts"
Write-Host "  (then send a JSON-RPC call on stdin)"
Write-Host ""
Write-Host "Environment variables (optional overrides):"
Write-Host "  WCR_HEADLESS=0              run headed (visible browser)"
Write-Host "  WCR_PROFILE_DIR=<path>      custom persistent profile directory"
Write-Host "  WCR_QUOTA_PER_HOUR=10       soft quota per provider per hour"
Write-Host "  PROJECT_LOG_DIR=<path>      write events to this devlog.sqlite directory"
