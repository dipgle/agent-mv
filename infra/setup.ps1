# Bootstrap video pipeline on Windows 10/11.
# Idempotent. Run from project root in PowerShell as regular user.
#
#   PS> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   PS> .\infra\setup.ps1
#
# Prereqs:
#   - winget (preinstalled on Win 11 / installable on Win 10)
#   - NVIDIA GPU with current driver + CUDA 12.x (verify: nvidia-smi)
#
# Steps:
#   1. Install Python 3.11, Git, ffmpeg, sqlite via winget
#   2. Install Ollama for Windows + pull text models
#   3. Clone ComfyUI + pip install
#   4. Download visual/audio weights from HuggingFace
#   5. Create Python venv + pip install requirements.txt

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$ComfyDir = Join-Path $ProjectDir "infra\comfy\ComfyUI"

Write-Host "Project: $ProjectDir" -ForegroundColor Cyan
Write-Host ""

# ─── 1. System deps via winget ───────────────────────────────────────────
$WingetDeps = @(
    "Python.Python.3.11",
    "Git.Git",
    "Gyan.FFmpeg",
    "SQLite.SQLite",
    "Ollama.Ollama"
)
foreach ($pkg in $WingetDeps) {
    Write-Host "Installing $pkg ..." -ForegroundColor Yellow
    winget install --id $pkg --silent --accept-package-agreements --accept-source-agreements 2>$null
}

# Reload PATH so we can use the newly installed binaries this session
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")

# ─── 2. Ollama text models ───────────────────────────────────────────────
Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden -PassThru | Out-Null
Start-Sleep -Seconds 3

$TextModels = @(
    "qwen2.5-vl:7b",
    "qwen3:8b",
    "qwen3:32b",
    "deepseek-r1:14b"
)
foreach ($m in $TextModels) {
    Write-Host "Pulling Ollama: $m" -ForegroundColor Yellow
    ollama pull $m
}

# ─── 3. ComfyUI install ──────────────────────────────────────────────────
if (-not (Test-Path $ComfyDir)) {
    Write-Host "Cloning ComfyUI to: $ComfyDir" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path (Split-Path $ComfyDir -Parent) | Out-Null
    git clone https://github.com/comfyanonymous/ComfyUI $ComfyDir
    Push-Location $ComfyDir
    python -m venv venv
    & "$ComfyDir\venv\Scripts\Activate.ps1"
    # Install PyTorch with CUDA 12.4 (adjust if your CUDA differs)
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
    pip install -r requirements.txt
    deactivate
    Pop-Location
}

# ─── 4. Visual / audio weights (HuggingFace) ─────────────────────────────
# All weights below are commercial-OK (Apache 2.0 / MIT).
# Disk requirement: ~45 GB total (flux-schnell ~12 GB + wan2.1 ~28 GB + f5 ~1 GB + whisper ~3 GB).
$HFModels = @(
    # Image keyframe gen — FLUX.1-schnell (Apache 2.0, 4-step distilled, ~12 GB)
    # Replaces FLUX.1-dev (non-commercial). See docs/migration-flux-dev-to-schnell.md.
    @{repo="black-forest-labs/FLUX.1-schnell"; dst="models\checkpoints\flux_schnell"},

    # Image-to-video motion — Wan2.1-T2V-14B (Apache 2.0, ~28 GB)
    # Replaces LTX-Video (research-only license).
    @{repo="Wan-AI/Wan2.1-T2V-14B"; dst="models\checkpoints\wan"},

    # Voice TTS — F5-TTS (Apache 2.0, ~1 GB)
    @{repo="SWivid/F5-TTS"; dst="models\tts\f5"},

    # Captions STT — Whisper large-v3 (MIT, ~3 GB)
    @{repo="openai/whisper-large-v3"; dst="models\stt\whisper"}

    # Stable Audio Open REMOVED — CC-BY-NC license not compatible with commercial use.
    # Music is now sourced via Pixabay API (royalty-free) or CC0 fallback library.
    # See orchestrator/lib/stock_music.py and docs/conventions.md "License hygiene".
)

# huggingface-cli ships in venv; ensure it's available
& "$ComfyDir\venv\Scripts\Activate.ps1"
pip install --quiet huggingface_hub hf_transfer
$env:HF_HUB_ENABLE_HF_TRANSFER = "1"

foreach ($m in $HFModels) {
    $full = Join-Path $ComfyDir $m.dst
    if ((Test-Path $full) -and ((Get-ChildItem $full).Count -gt 0)) {
        Write-Host "Skip (exists): $($m.repo)" -ForegroundColor Gray
        continue
    }
    Write-Host "Downloading: $($m.repo) -> $($m.dst)" -ForegroundColor Cyan
    huggingface-cli download $m.repo --local-dir $full
}
deactivate

# ─── 5. Project Python venv ──────────────────────────────────────────────
Push-Location $ProjectDir
if (-not (Test-Path "venv")) { python -m venv venv }
& "$ProjectDir\venv\Scripts\Activate.ps1"
pip install --quiet --upgrade pip
pip install -r requirements.txt

# Copy .env from example if missing
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example — edit to add cloud API keys (optional)" -ForegroundColor Yellow
}
deactivate
Pop-Location

# ─── 6. Verify ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Done. Boot services in 3 separate PowerShell windows: ===" -ForegroundColor Green
Write-Host "  1. ollama serve"
Write-Host "  2. cd $ComfyDir; .\venv\Scripts\Activate.ps1; python main.py --listen"
Write-Host "  3. cd $ProjectDir; .\venv\Scripts\Activate.ps1; litellm --config infra\litellm.yaml --port 4000"
Write-Host ""
Write-Host "Then run pipeline:" -ForegroundColor Green
Write-Host "  .\venv\Scripts\Activate.ps1"
Write-Host "  python orchestrator\pipeline.py --intent ""TikTok 30s SaaS analytics"" --feature-id VID-001"
