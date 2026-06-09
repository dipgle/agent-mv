# Troubleshooting Runbook

This guide helps diagnose and fix common issues when running the video pipeline.

## Section 1: Services Won't Start

### Ollama not responding on :11434

**Symptoms:**
- Pipeline fails with `ConnectionError: connect to Ollama on localhost:11434`
- LiteLLM proxy can't reach local models

**Diagnosis:**
```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# If connection refused, try starting Ollama
ollama serve
```

**Fix:**
1. **Ollama not installed**: Follow [docs/INSTALL-WIN.md](INSTALL-WIN.md) / [INSTALL-MAC.md](INSTALL-MAC.md) / [INSTALL-LINUX.md](INSTALL-LINUX.md)
2. **Port conflict**: Check what's on :11434
   ```bash
   # macOS/Linux
   lsof -i :11434
   
   # Windows
   netstat -ano | findstr :11434
   ```
   Kill the conflicting process or change Ollama `--port` flag.

3. **GPU driver issue** (especially macOS/NVIDIA):
   ```bash
   # Verify GPU access
   ollama list
   
   # If models show but inference slow, check VRAM
   # macOS: Activity Monitor → Memory
   # Windows: Task Manager → Performance → GPU
   # Linux: nvidia-smi
   ```

### ComfyUI not responding on :8188

**Symptoms:**
- Pipeline fails with `ConnectionError` to ComfyUI
- `/api/prompt` returns 404

**Diagnosis:**
```bash
# Check ComfyUI running
curl http://localhost:8188/api/system_stats

# Expected: JSON with memory, VRAM, etc.
```

**Fix:**
1. **ComfyUI not started**:
   ```bash
   cd infra/comfy/ComfyUI
   python main.py --listen 0.0.0.0 --port 8188
   ```

2. **Missing Python dependencies in ComfyUI venv**:
   ```bash
   cd infra/comfy/ComfyUI
   # Activate ComfyUI's venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
   pip install -r requirements.txt
   ```

3. **CUDA/torch mismatch**:
   - NVIDIA RTX: requires CUDA 12.1, ensure `torch` and `torchvision` match
   - Apple M-series: torch must be `pt>=2.0` with MPS support
   - Run `python -c "import torch; print(torch.cuda.is_available())"` to verify

4. **Port conflict**:
   ```bash
   # macOS/Linux: find what's on 8188
   lsof -i :8188
   
   # Windows
   netstat -ano | findstr :8188
   ```

### LiteLLM proxy not responding on :4000

**Symptoms:**
- Pipeline fails with `ConnectionError` to `:4000`
- LiteLLM logs show error loading YAML config

**Diagnosis:**
```bash
# Check if LiteLLM is running
curl http://localhost:4000/health

# View LiteLLM startup logs
```

**Fix:**
1. **Verify litellm.yaml syntax**:
   ```bash
   python -c "import yaml; yaml.safe_load(open('infra/litellm.yaml'))" && echo "OK"
   ```
   If error, check YAML indentation (LiteLLM is sensitive to YAML format).

2. **Missing environment variables**:
   - LiteLLM reads `.env` for cloud API keys
   - Copy `.env.example` → `.env` and fill in keys
   ```bash
   cp .env.example .env
   # Edit .env with your OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
   ```

3. **Restart LiteLLM with fresh config**:
   ```bash
   # Kill existing process
   pkill -f litellm
   
   # Start fresh
   litellm --config infra/litellm.yaml --port 4000 --debug
   ```

## Section 2: Pipeline Render Fails Midway

### Checkpoint system — resuming from where you left off

When the pipeline is interrupted mid-render (crash, OOM, network drop, Ctrl-C)
it writes a `.checkpoint.json` file inside the feature output directory.  On the
next invocation with the same `--feature-id`, the pipeline automatically loads
this file and skips every step that already completed successfully.

**Default behavior (--resume)**

```bash
# Re-run exactly as before — the pipeline picks up from the last successful step.
python orchestrator/pipeline.py \
    --intent "TikTok 30s SaaS analytics intro" \
    --feature-id VID-001 \
    --brand brand-example.json
```

The terminal will print a banner like:
```
[checkpoint] Resuming from step after: shot_06_motion  (attempt 1, 14 steps already done)
```

**Inspect the checkpoint without re-rendering**

```bash
python orchestrator/pipeline.py \
    --intent "..." \
    --feature-id VID-001 \
    --show-checkpoint
```

Output:
```
Checkpoint: out/VID-001/.checkpoint.json
  feature_id : VID-001
  started_at : 2026-06-09T08:14:22
  attempt_n  : 1
  last_step  : shot_06_motion

  Completed steps:
    researcher  ->  out/VID-001/reference.json
    planner  ->  out/VID-001/shotlist.json
    shot_01_keyframe  ->  out/VID-001/shots/01_keyframe.png
    ...
    shot_06_motion  ->  out/VID-001/shots/06_clip.mp4
```

**Force a full re-render (ignore checkpoint)**

```bash
python orchestrator/pipeline.py \
    --intent "..." \
    --feature-id VID-001 \
    --force-redo
```

**Restart from a specific step**

Re-runs the named step and everything after it; steps before it are preserved.

```bash
# Re-run from shot_04_keyframe onward (shot 4, 5, 6... + audio + compose + review)
python orchestrator/pipeline.py \
    --intent "..." \
    --feature-id VID-001 \
    --from-step shot_04_keyframe
```

Valid step IDs:
- `researcher`
- `planner`
- `shot_NN_keyframe` / `shot_NN_motion`  (NN = zero-padded index, e.g. `03`)
- `voice`, `music`, `caption`
- `compose`

**Crash threshold**

If a step crashes (ComfyUI 500, connection refused, OOM) the pipeline logs a
`kind='step_crashed'` event and immediately stops so the checkpoint is not
corrupted.  Use `--max-crashes` to set how many crashes are tolerated in a
single invocation before aborting (default 3):

```bash
python orchestrator/pipeline.py \
    --intent "..." \
    --feature-id VID-001 \
    --max-crashes 5
```

**High attempt-count warning**

If a feature has been through more than 5 Reviewer-reject cycles, the pipeline
prints a warning at startup and suggests manual inspection.  This is purely
informational — the render continues unless you stop it.

**Checkpoint and reviewer re-render loop**

When the Reviewer rejects a video and flags specific shots for re-render, the
pipeline *only* invalidates those shots in the checkpoint.  All other completed
steps (especially audio, which is expensive) are preserved and skipped on the
next iteration.

**Devlog views for checkpoint events**

```sql
-- See which steps were skipped (checkpoint hits)
SELECT * FROM step_skips WHERE feature_id = 'VID-001';

-- See crash events with error class
SELECT * FROM step_crashes WHERE feature_id = 'VID-001';

-- See artifact-path repairs (file deleted between runs)
SELECT * FROM checkpoint_repairs WHERE feature_id = 'VID-001';
```

### "Stub workflow" error from comfy_client.py

**Symptoms:**
```
Error: ComfyUI workflow is a stub (JSON name ends in .stub)
```

**Diagnosis:**
Check `workflows/` folder:
```bash
ls -la workflows/*.json*
```

You'll see files ending in `.stub` — these are placeholders.

**Fix:**
Export real workflows from ComfyUI UI:
1. Open ComfyUI browser (http://localhost:8188)
2. Load example workflow for the modality (Manager → Examples)
3. Configure node inputs as needed
4. Right-click canvas → "Save (API Format)"
5. Save to `workflows/` matching the modality name (without `.stub`):
   - `flux_keyframe.json` (not `.stub`)
   - `ltx_motion.json` (not `.stub`)
   - `f5_tts.json` (not `.stub`)
   - `stable_audio_music.json` (not `.stub`)
   - `whisper_caption.json` (not `.stub`)

After exporting, the pipeline will find and use the real workflows.

### ComfyUI workflow node IDs mismatch

**Symptoms:**
```
Error: Node ID "6" not found in workflow
```

**Diagnosis:**
When you export a workflow from ComfyUI, node IDs depend on your specific setup. The pipeline assumes certain node IDs (e.g., "6" = CLIPTextEncode for prompts).

**Fix:**
1. Export your workflow from ComfyUI
2. Open the JSON and note your node IDs:
   ```bash
   # Example: find node with class_type = "CLIPTextEncode"
   grep -n "CLIPTextEncode" workflows/flux_keyframe.json
   ```
   The ID is the key, e.g., `"6": { "inputs": {...}, "class_type": "CLIPTextEncode" }`

3. Update `orchestrator/pipeline.py` in the Executor phase:
   ```python
   # Search for "NODE IDs depend on"
   patches = {
       "6": {"text": shot["image_prompt"]},      # YOUR_CLIPTEXT_ID
       "5": {"width": w, "height": h},           # YOUR_LATENT_ID
       "4": {"ckpt_name": model_variant},        # YOUR_CHECKPOINT_LOADER_ID
       # ... etc
   }
   ```
   Replace the IDs with your actual node IDs from the exported JSON.

See `infra/models.md` for mapping examples per model.

### Out Of Memory (OOM) mid-render

**Symptoms:**
```
CUDA out of memory error
RuntimeError: CUDA out of memory. Tried to allocate X GB
```

**Diagnosis:**
Check current VRAM usage:
```bash
# NVIDIA
nvidia-smi

# macOS (unified memory)
# Activity Monitor → Memory tab

# Linux
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,nounits
```

**Fix:**
1. **Reduce resolution**:
   In pipeline call, lower `--resolution` (default 1024):
   ```bash
   python orchestrator/pipeline.py \
       --intent "..." \
       --feature-id "..." \
       --resolution 768  # instead of 1024
   ```

2. **Use quantized model variant**:
   In `infra/litellm.yaml` or model selection, switch to Q4 GGUF:
   ```yaml
   # Instead of:
   - model_name: flux-dev
     litellm_params:
       model: local_models/flux.1-dev  # full precision

   # Use:
   - model_name: flux-dev-q4
     litellm_params:
       model: local_models/flux.1-dev-q4.gguf  # quantized
   ```

3. **Free up memory**:
   - Restart ComfyUI (clears VRAM)
   - Close other VRAM-intensive apps
   - On shared GPU: check `nvidia-smi` for other processes

4. **Split into smaller batch**:
   Generate fewer shots per render, then compose separately.

## Section 3: Reviewer Always Rejects

### All Tier 1 checks failing

**Symptoms:**
Reviewer log shows:
```
CRITICAL: ffmpeg missing
CRITICAL: pyloudnorm missing
CRITICAL: opencv missing
```

**Diagnosis:**
```bash
# Check if required binaries/packages exist
which ffmpeg || echo "ffmpeg not found"
python -c "import pyloudnorm" && echo "OK" || echo "Missing"
python -c "import cv2" && echo "OK" || echo "Missing"
```

**Fix:**
Install missing dependencies:
```bash
# ffmpeg (macOS)
brew install ffmpeg

# ffmpeg (Linux)
sudo apt-get install ffmpeg

# ffmpeg (Windows)
# Download from https://ffmpeg.org/download.html or via winget
winget install ffmpeg

# Python packages
pip install pyloudnorm opencv-python
```

### LLM panel timeout (Reviewer hangs)

**Symptoms:**
Reviewer phase times out, reviewer never completes.

**Diagnosis:**
```bash
# Check Ollama health
curl http://localhost:11434/api/tags

# Check if model is loaded
ollama list

# Monitor Ollama logs during review
ollama logs -f
```

**Fix:**
1. **Ollama not responding**: Restart it
   ```bash
   pkill -9 ollama
   ollama serve &
   ```

2. **Model not loaded in memory**: Pull the model
   ```bash
   ollama pull deepseek-r1:14b
   ```

3. **Reviewer model misconfigured**: Check `infra/models.md` for mapping
   - Reviewer uses `deepseek-r1:14b` (reasoning) + `qwen2.5-vl:7b` (vision)
   - If these aren't available, fall back to local alternatives

4. **Cascade fallback broken**: Check `eval/breakers.json`
   ```bash
   cat eval/breakers.json
   ```
   If a breaker is "open" (circuit breaker pattern), it's paused due to repeated failures. Reset:
   ```bash
   # Delete breaker to reset
   rm eval/breakers.json
   ```

## Section 4: Cost Gate Keeps Tripping

**Symptoms:**
Pipeline halts with:
```
CostGate: Exceeded per-video cap ($X > $5.00)
```

### Reading Recent Cost Decisions

```bash
# View recent cost gate decisions
sqlite3 logs/devlog.sqlite \
  "SELECT ts, actor, content FROM events \
   WHERE kind='decision' AND actor='cost_gate' \
   ORDER BY ts DESC LIMIT 20"
```

Example output:
```
2026-06-09 14:32:01 cost_gate | Exceeded per-video: $7.50 > $5.00 → fallback to qwen3:8b
2026-06-09 14:30:45 cost_gate | Day budget OK: $32.50 < $50.00
```

### Adjusting Cost Caps

Environment variables control budgets (set in `.env` or shell):

```bash
# Per-video cap
export MAX_COST_PER_VIDEO_USD=10   # default 5

# Per-day cap
export MAX_COST_PER_DAY_USD=100    # default 50

# Per-month cap
export MAX_COST_PER_MONTH_USD=1000 # default 500
```

Then restart the pipeline:
```bash
python orchestrator/pipeline.py \
    --intent "..." \
    --feature-id "..." \
    --max-cost-per-video 10
```

### Understanding Cascade Behavior

When cost exceeds cap, pipeline cascades to cheaper models:
1. **Planner script**: `qwen3:32b` (local) → `deepseek-r1:14b` (local) → `groq` (free API) → `Claude Sonnet` (paid)
2. **Reviewer**: `deepseek-r1:14b` → `qwen3:8b` → `Claude Sonnet`
3. **Visual (Keyframe)**: Cost doesn't cascade (no cheaper free option) — render cheaper resolution instead

View the cascade config in `infra/litellm.yaml`.

## Section 5: Supervisor Not Running

### Cron jobs not firing (daily/weekly)

**Symptoms:**
- No `eval/reports/audit_*.md` files generated
- No `eval/reports/cost_*.md` appearing
- No `eval/reports/scan_*.md` (weekly)

#### On Linux/macOS

**Diagnosis:**
```bash
# Check if cron is running
crontab -l

# Expected output:
# 0 2 * * * cd /path/to/agent-mv && bash orchestrator/cron/daily.sh > logs/cron-daily.log 2>&1
# 0 9 * * 1 cd /path/to/agent-mv && bash orchestrator/cron/weekly.sh > logs/cron-weekly.log 2>&1
```

**Fix:**
1. **Add missing cron jobs**:
   ```bash
   crontab -e
   # Paste the two lines above (adjust path to your project)
   # Save and exit
   ```

2. **Check cron daemon running**:
   ```bash
   # macOS
   sudo launchctl list | grep cron
   
   # Linux
   sudo systemctl status cron
   ```

3. **Check cron logs** for errors:
   ```bash
   # macOS
   log stream --predicate 'eventMessage contains[c] "cron"'
   
   # Linux
   sudo tail -f /var/log/syslog | grep CRON
   ```

4. **Check supervisor script logs**:
   ```bash
   tail -f logs/cron-daily.log
   tail -f logs/cron-weekly.log
   ```

#### On Windows

**Diagnosis:**
```powershell
# Check if scheduled tasks exist
Get-ScheduledTask -TaskName "AgentMV-*"

# Expected: two tasks
# AgentMV-daily (2:00 AM daily)
# AgentMV-weekly (9:00 AM every Monday)
```

**Fix:**
1. **Add missing scheduled tasks**:
   Follow [docs/INSTALL-WIN.md](INSTALL-WIN.md) Section 9 to create tasks via `schtasks` or Task Scheduler GUI.

2. **Verify task runs**:
   ```powershell
   # Run manually to test
   & ".\orchestrator\cron\daily.ps1"
   & ".\orchestrator\cron\weekly.ps1"
   ```

3. **Check execution logs**:
   ```powershell
   # View past runs
   Get-ScheduledTaskInfo -TaskName "AgentMV-daily"
   
   # View errors
   Get-WinEvent -Path "C:\Windows\System32\winevt\Logs\System.evtx" | Where-Object {$_.Message -like "*AgentMV*"}
   ```

### Manual Supervisor Run

To trigger supervisor jobs manually:

```bash
# Linux/macOS
bash orchestrator/cron/daily.sh   # audit + cost rollup + auto-promote
bash orchestrator/cron/weekly.sh  # external scan + proposals

# Windows
.\orchestrator\cron\daily.ps1
.\orchestrator\cron\weekly.ps1
```

Expected output: `eval/reports/` populated with `.md` files.

## Section 6: Dashboard Empty

### `eval/serve.py` not running

**Diagnosis:**
```bash
# Check if serve.py is running (default port 7891)
curl http://localhost:7891/health
# Expected: {"ok": true, "schema_version": 2, "uptime_s": ..., "requests_last_min": ...}
```

**Fix:**
1. **Start the dashboard server**:
   ```bash
   python eval/serve.py
   # Or with a custom port:
   python eval/serve.py --port 8765
   ```

2. **Verify port 7891 is free**:
   ```bash
   # macOS/Linux
   lsof -i :7891
   
   # Windows
   netstat -ano | findstr :7891
   ```

3. **Try alternate port**:
   ```bash
   python eval/serve.py --port 8001
   # Then visit http://localhost:8001/
   ```

### Auth troubleshooting (401 Unauthorized)

`eval/serve.py` runs in two modes controlled by the `EVAL_SERVE_TOKEN` environment variable.

#### Mode 1: Localhost-only (default, no token needed)

When `EVAL_SERVE_TOKEN` is **not set**:
- Server binds to `127.0.0.1` only (any `--host` argument is silently overridden)
- No `Authorization` header is required
- Rate limit: 100 req/min per IP

```bash
# Start in localhost-only mode (default)
python eval/serve.py
# Access: http://localhost:7891/
```

#### Mode 2: Auth-required (LAN / internet-exposed)

When `EVAL_SERVE_TOKEN` **is set**:
- Server accepts the `--host` argument (can bind 0.0.0.0 or a specific interface)
- Every `/eval/api/*` request must include `Authorization: Bearer <token>`
- Public endpoints that bypass auth: `/health`, `/` (dashboard HTML)
- Rate limit: 100 req/min for authenticated requests, 10 req/min for unauthenticated

```bash
# Generate a strong token
TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
echo "EVAL_SERVE_TOKEN=$TOKEN" >> .env

# Start in auth mode (accessible from LAN or internet)
EVAL_SERVE_TOKEN=$TOKEN python eval/serve.py --host 0.0.0.0 --port 7891
```

**Dashboard prompt**: When the server requires auth, the dashboard HTML shows a
token-input modal automatically on first load. Enter the same value you set for
`EVAL_SERVE_TOKEN`. The token is kept in `sessionStorage` (clears on tab close).

**curl test**:
```bash
# Health check — always public (no token needed)
curl http://yourhost:7891/health

# API endpoint — requires token
TOKEN="your-token-here"
curl -H "Authorization: Bearer $TOKEN" http://yourhost:7891/eval/api/cost/mtd
```

**Common 401 causes**:
| Symptom | Cause | Fix |
|---|---|---|
| `curl /eval/api/cost/mtd` → 401 | Token not passed | Add `-H "Authorization: Bearer <token>"` |
| Dashboard modal appears on every refresh | sessionStorage cleared (incognito / tab close) | Re-enter token in the modal |
| `curl /health` → 401 unexpectedly | Reverse proxy stripping responses | Check proxy config; /health must be passed through |
| Wrong token error | Mismatch between server env var and what you typed | Verify with `echo $EVAL_SERVE_TOKEN` on the server |

**Rate limit 429**: If you hit `{"error": "Too Many Requests"}`, wait 60 seconds.
Authenticated sessions get 100 req/min; unauthenticated (localhost mode) get 10 req/min.

**Access log** for debugging (rotates at 10 MB, keeps 3 backups):
```bash
tail -f logs/eval_serve_access.log
# Format: [ISO8601] METHOD PATH STATUS LATENCY_MS BYTES IP TOKEN_HASH[:8]
# TOKEN_HASH is SHA-256[:8] — safe to share, never the raw token
```

### Devlog empty (no data to display)

**Symptoms:**
Dashboard loads but all metrics are zero.

**Diagnosis:**
```bash
# Check if devlog has any events
sqlite3 logs/devlog.sqlite "SELECT COUNT(*) FROM events"

# If 0, devlog is empty
```

**Fix:**
1. **Run a smoke video render** to populate logs:
   ```bash
   python orchestrator/pipeline.py \
       --intent "Test 5s clip" \
       --feature-id SMOKE-001 \
       --duration 5
   ```
   This generates events logged to devlog.

2. **Run supervisor to generate audit/cost reports**:
   ```bash
   bash orchestrator/cron/daily.sh
   ```

3. **Refresh dashboard** in browser (Ctrl+Shift+R or Cmd+Shift+R)

### Wrong port in dashboard.html

If dashboard opens but shows "connection refused":

```bash
# Edit dashboard.html and check the fetch URL
grep "fetch\|http://localhost" eval/dashboard.html

# Should match your serve.py --port (default 5000)
```

Update the URL in dashboard.html if you're using a different port.

---

## General Troubleshooting

### Full system check

Run the environment validator:

```bash
# Linux/macOS
bash orchestrator/_console.py check --verbose

# Windows
.\orchestrator\_console.py check --verbose
```

Outputs:
```
[OK] Python 3.11
[OK] ffmpeg 6.0
[OK] Ollama 0.1.35
[WARN] ComfyUI not running (expected if not started yet)
[OK] SQLite 3.42
```

### Enable debug logging

```bash
# Add to .env
DEBUG=1

# Then restart pipeline
python orchestrator/pipeline.py --intent "..." --feature-id "..." --debug
```

Logs to stdout with timestamp + module name.

### Check resource usage during render

```bash
# In separate terminal, monitor while render is happening
watch -n 1 'nvidia-smi | head -20'   # NVIDIA GPU
# or
watch -n 1 'free -h'                  # Memory
```

---

See also:
- [docs/architecture.md](architecture.md) — role and tier overview
- [docs/conventions.md](conventions.md) — golden rules
- [infra/models.md](../infra/models.md) — model inventory and timings
