# Migration: FLUX.1-dev → FLUX.1-schnell

This guide is for users who previously installed FLUX.1-dev and want to migrate
to the new commercial-OK default stack (FLUX.1-schnell + Wan2.1-T2V-14B).

## Why migrate

| Aspect | FLUX.1-dev | FLUX.1-schnell |
|---|---|---|
| License | BFL Non-Commercial | Apache 2.0 |
| Commercial use | NOT permitted | Permitted |
| Disk size | ~24 GB | ~12 GB |
| Inference steps | 20-28 steps | 4 steps |
| Relative speed | 1× (baseline) | ~7× faster |
| Quality vs dev | — | ~85% |
| CFG scale | Used (typically 3.5-7.5) | Not used (distilled) |

FLUX.1-schnell is a distilled variant of FLUX.1-dev. It was trained to match
dev output in 4 steps instead of 20-28. For production video pipelines where
throughput matters, the 7× speedup outweighs the modest quality reduction.

## Step 1 — Download FLUX.1-schnell weights

If you ran the updated `infra/setup.sh` (or `setup.ps1`), the weights are
already downloaded to `comfy/ComfyUI/models/checkpoints/flux_schnell/`.

If not, run manually:
```bash
# Mac / Linux
huggingface-cli download black-forest-labs/FLUX.1-schnell \
    --local-dir infra/comfy/ComfyUI/models/checkpoints/flux_schnell

# Windows (in activated venv)
huggingface-cli download black-forest-labs/FLUX.1-schnell `
    --local-dir infra\comfy\ComfyUI\models\checkpoints\flux_schnell
```

## Step 2 — Free disk: delete FLUX.1-dev weights

After verifying schnell works, delete the dev weights to reclaim ~12 GB:
```bash
# Mac / Linux
rm -rf infra/comfy/ComfyUI/models/checkpoints/flux/

# Windows
Remove-Item -Recurse -Force infra\comfy\ComfyUI\models\checkpoints\flux\
```

## Step 3 — Update your ComfyUI workflow

The existing `flux_keyframe.json` (if you had one) was built for FLUX.1-dev.
You need to export a new workflow for Schnell.

Key differences in the ComfyUI workflow:

| Node | FLUX.1-dev setting | FLUX.1-schnell setting |
|---|---|---|
| KSampler — steps | 20-28 | **4** |
| KSampler — cfg | 3.5-7.5 | **0.0** (ignored by distilled model) |
| KSampler — sampler | euler / dpm++ | **euler** |
| KSampler — scheduler | karras / exponential | **simple** |
| Negative prompt | Used | **Not used** (leave empty or remove) |
| Checkpoint | `flux/` folder | **`flux_schnell/`** folder |

Steps to rebuild the workflow:
1. Open ComfyUI: `http://localhost:8188`
2. Load your old `flux_keyframe.json` workflow
3. Change the checkpoint node to point to `flux_schnell/`
4. Update KSampler: steps=4, cfg=0.0, scheduler=simple
5. Remove or disconnect the negative prompt node
6. Test render a prompt (e.g., "blue sky, mountains, photorealistic")
7. File → Save (API Format) → `workflows/flux_schnell_keyframe.json`
8. Delete `workflows/flux_keyframe.json` (no longer used)

## Step 4 — Update node ID mapping in pipeline.py

The node IDs in your new workflow export may differ from the old one.
Find the `executor_keyframe` function in `orchestrator/pipeline.py` and
update the `patches` dict to match your new workflow's node IDs:

```python
patches = {
    "YOUR_CLIP_NODE_ID":   {"text": shot["image_prompt"]},
    "YOUR_LATENT_NODE_ID": {"width": width, "height": height},
    "YOUR_KSAMPLER_ID":    {"seed": shot["idx"] * 1000,
                             "steps": 4, "cfg": 0.0,
                             "sampler_name": "euler",
                             "scheduler": "simple"},
}
```

Node IDs are visible in ComfyUI by right-clicking any node → "Copy (Legacy)".

## Step 5 — Smoke test

```bash
# Activate venv and run pipeline in dry mode (5s test clip)
python orchestrator/pipeline.py \
    --intent "Test blue sky 5s" \
    --feature-id MIGRATE-TEST-001 \
    --aspect 16:9 \
    --duration 5
```

Expected: `out/MIGRATE-TEST-001/shots/01_keyframe.png` generated in ~3-5s
(vs ~25s with dev on the same hardware).

## Quality expectations

- **Composition**: comparable to dev for single-subject prompts.
- **Fine detail**: slightly lower fidelity on complex scenes (text in image,
  intricate textures). For hero shots, consider 2-3 seeds and pick best.
- **Prompt adherence**: strong. Schnell was trained to match dev semantics.
- **CFG nuance**: with cfg=0.0 there is no negative prompt effect. Work
  around by being more specific in the positive prompt rather than using
  negative prompts ("soft natural lighting" instead of "no harsh lighting").

For personal/research work where dev quality is strictly required, you can
set `COMMERCIAL_MODE=0` in your `.env` to re-enable the FLUX.1-dev path
(see `docs/conventions.md` "License hygiene"). This should NEVER be used
for any commercial or client deliverable.

## LTX-Video → Wan2.1-T2V-14B

The same session also replaces LTX-Video with Wan2.1-T2V-14B for the
image-to-video step. Download instructions:

```bash
huggingface-cli download Wan-AI/Wan2.1-T2V-14B \
    --local-dir infra/comfy/ComfyUI/models/checkpoints/wan
```

Then export a new `workflows/wan21_motion.json` from ComfyUI following the
instructions in `workflows/wan21_motion.json.stub`.

Wan2.1 requires more VRAM (~24-40 GB) but produces significantly higher
motion quality than LTX-Video. For proxy renders (720p), VRAM drops to ~24 GB.
