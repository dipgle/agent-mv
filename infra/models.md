# Models Inventory — Video Pipeline

> **All weights listed here are commercial-OK.**
> See `docs/conventions.md` "License hygiene" for the verification process
> and the full list of allowed / blocked licenses.

## Text (Ollama → LiteLLM)

| Logical | Physical | Role | Size | License |
|---|---|---|---|---|
| `planner` | `ollama/qwen3:32b` | Planner script | 20 GB | Apache 2.0 |
| `reviewer` | `ollama/deepseek-r1:14b` | Reviewer text | 9 GB | MIT |
| `researcher-vision` | `ollama/qwen2.5-vl:7b` | VL parse refs | 5 GB | Apache 2.0 |
| `researcher-text` | `ollama/qwen3:8b` | Text reason | 5 GB | Apache 2.0 |

## Visual gen (ComfyUI, direct call)

| Modality | Model | Path | VRAM | License |
|---|---|---|---|---|
| Image (default) | `flux.1-schnell` (4-step distilled) | `comfy/.../flux_schnell/` | 12 GB | Apache 2.0 |
| Image alt | `sdxl-turbo` | `comfy/.../sdxl/` | 6 GB | CreativeML OpenRAIL-M (personal OK; verify commercial use) |
| Image-to-video (default) | `Wan2.1-T2V-14B` | `comfy/.../wan/` | 24-40 GB | Apache 2.0 |
| Lip sync (optional) | `LatentSync` / `MuseTalk` | `comfy/.../lipsync/` | 8-16 GB | Apache 2.0 |
| Upscale | `Real-ESRGAN` + `RIFE` | `comfy/.../upscale/` | 4 GB | BSD 3-Clause |

**Removed (non-commercial):**
- `flux.1-dev` — BFL Non-Commercial license. Replaced by `flux.1-schnell` (Apache 2.0).
- `LTX-Video` — Lightricks Research license. Replaced by `Wan2.1-T2V-14B` (Apache 2.0).

## Audio gen

| Modality | Model / Source | Path | VRAM | License |
|---|---|---|---|---|
| TTS | `F5-TTS` | `comfy/.../tts/f5/` | 4 GB | Apache 2.0 |
| TTS alt | `XTTS-v2` | (separate Coqui install) | 4 GB | CPML (verify commercial) |
| Music (default) | Pixabay Music API | HTTP — no local weights | — | Royalty-free commercial OK |
| Music fallback | CC0 library | `data/stock_music_fallback/` | — | CC0 Public Domain |
| STT (captions) | `Whisper large-v3` | `comfy/.../stt/whisper/` | 3 GB | MIT |

**Removed (non-commercial):**
- `Stable Audio Open` — Stability AI CC-BY-NC license. Replaced by Pixabay API + CC0 fallback.
- `MusicGen-Large` — CC-BY-NC license. Not included.

## Cloud escalation (paid, optional)

| Modality | Provider | Cost | Use |
|---|---|---|---|
| Video gen | Runway Gen-3 | $0.50-2/clip | Premium quality, paying client |
| Video gen | Pika 2.0 | $0.30-1.50/clip | Stylized alt |
| Voice clone | ElevenLabs | $0.30/min | Hard clone case |
| Music | Suno / Udio | $0.10/song | Branded jingle (verify current TOS) |
| TTV (text-to-video) | Sora API / Veo | $$$$ | Future, very expensive |

## Frontier (Tier S text-only)

| Logical | Provider | Use |
|---|---|---|
| `planner-script-hard` | Claude Opus 4.7 | Narrative iterate, taste arbiter |
| `reviewer-frames` | Claude Opus 4.7 | Vision review 6 frames + transcript |
| `adjudicator` | Claude Opus 4.7 | Brand match tie-break |
| `architect` | Claude Opus 4.7 | Campaign concept |

## Disk footprint summary

| Component | Size | Notes |
|---|---|---|
| FLUX.1-schnell | ~12 GB | Down from ~24 GB (dev variant) |
| Wan2.1-T2V-14B | ~28 GB | Quality alternative to LTX-Video |
| F5-TTS | ~1 GB | Unchanged |
| Whisper large-v3 | ~3 GB | Unchanged |
| Ollama text models | ~39 GB | Unchanged |
| **Total weights** | **~45 GB** | Was ~60 GB before refactor |

## Update log

| Date | Change | Reason |
|---|---|---|
| 2026-06-09 | Initial inventory | Bootstrap |
| 2026-06-09 | Swap to commercial-clean stack | FLUX.1-schnell + Wan2.1 + Pixabay music; drop FLUX.1-dev / LTX-Video / Stable Audio Open |
