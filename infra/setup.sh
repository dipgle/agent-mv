#!/usr/bin/env bash
# Bootstrap video pipeline: Ollama text models + ComfyUI + visual/audio weights.
# Idempotent.

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMFY_DIR="$PROJECT_DIR/infra/comfy/ComfyUI"

# ─── 1. Ollama text models ───────────────────────────────────────────────
TEXT_MODELS=(
  "qwen2.5-vl:7b"            # Researcher vision
  "qwen3:8b"                 # Researcher text
  "qwen3:32b"                # Planner (with thinking)
  "deepseek-r1:14b"          # Reviewer
)
for m in "${TEXT_MODELS[@]}"; do
  echo "Pulling Ollama: $m"
  ollama pull "$m"
done

# ─── 2. ComfyUI install ──────────────────────────────────────────────────
if [ ! -d "$COMFY_DIR" ]; then
  echo "Cloning ComfyUI to: $COMFY_DIR"
  mkdir -p "$PROJECT_DIR/infra/comfy"
  git clone https://github.com/comfyanonymous/ComfyUI "$COMFY_DIR"
  cd "$COMFY_DIR"
  python -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
  cd "$PROJECT_DIR"
fi

# ─── 3. Visual / audio model weights ─────────────────────────────────────
# All weights below are commercial-OK (Apache 2.0 / MIT).
# Disk requirement: ~45 GB total (flux-schnell ~12 GB + wan2.1 ~28 GB + f5 ~1 GB + whisper ~3 GB).
# Pull big models manually — comment out what you don't need.
HF_MODELS=(
  # Image keyframe gen — FLUX.1-schnell (Apache 2.0, 4-step distilled, ~12 GB)
  # Replaces FLUX.1-dev (non-commercial). See docs/migration-flux-dev-to-schnell.md.
  "black-forest-labs/FLUX.1-schnell:models/checkpoints/flux_schnell"

  # Image-to-video motion — Wan2.1-T2V-14B (Apache 2.0, ~28 GB)
  # Replaces LTX-Video (research-only license).
  "Wan-AI/Wan2.1-T2V-14B:models/checkpoints/wan"

  # Voice TTS — F5-TTS (Apache 2.0, ~1 GB)
  "SWivid/F5-TTS:models/tts/f5"

  # Captions STT — Whisper large-v3 (MIT, ~3 GB)
  "openai/whisper-large-v3:models/stt/whisper"

  # Stable Audio Open REMOVED — CC-BY-NC license not compatible with commercial use.
  # Music is now sourced via Pixabay API (royalty-free) or CC0 fallback library.
  # See orchestrator/lib/stock_music.py and docs/conventions.md "License hygiene".
)
for entry in "${HF_MODELS[@]}"; do
  repo="${entry%%:*}"
  dst="${entry#*:}"
  full_dst="$COMFY_DIR/$dst"
  if [ -d "$full_dst" ] && [ "$(ls -A "$full_dst" 2>/dev/null)" ]; then
    echo "Skip (exists): $repo"
    continue
  fi
  echo "Downloading: $repo → $dst"
  huggingface-cli download "$repo" --local-dir "$full_dst"
done

# ─── 4. Tools check ──────────────────────────────────────────────────────
for tool in ffmpeg sqlite3; do
  if ! command -v $tool >/dev/null 2>&1; then
    echo "MISSING: $tool — install via brew"
  fi
done

echo ""
echo "Done. Boot services:"
echo "  ollama serve &"
echo "  cd $COMFY_DIR && source venv/bin/activate && python main.py --listen &"
echo ""
echo "Then run pipeline:"
echo "  python orchestrator/pipeline.py --intent '...' --feature-id VID-001"
