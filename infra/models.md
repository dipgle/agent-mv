# Models Inventory — Video Pipeline

## Text (Ollama → LiteLLM)

| Logical | Physical | Role | Size |
|---|---|---|---|
| `planner` | `ollama/qwen3:32b` | Planner script | 20 GB |
| `reviewer` | `ollama/deepseek-r1:14b` | Reviewer text | 9 GB |
| `researcher-vision` | `ollama/qwen2.5-vl:7b` | VL parse refs | 5 GB |
| `researcher-text` | `ollama/qwen3:8b` | Text reason | 5 GB |

## Visual gen (ComfyUI, direct call)

| Modality | Model | Path | VRAM |
|---|---|---|---|
| Image | `flux.1-dev` (Q4 GGUF) | `comfy/ComfyUI/models/checkpoints/flux/` | 12-18 GB |
| Image alt | `sdxl-turbo` | `comfy/.../sdxl/` | 6 GB |
| Image-to-video | `LTX-Video` | `comfy/.../ltx/` | 12-22 GB |
| Image-to-video alt | `Wan2.1-14B` | `comfy/.../wan/` (manual pull) | 24-40 GB |
| Lip sync (optional) | `LatentSync` / `MuseTalk` | `comfy/.../lipsync/` | 8-16 GB |
| Upscale | `Real-ESRGAN` + `RIFE` | `comfy/.../upscale/` | 4 GB |

## Audio gen

| Modality | Model | Path | VRAM |
|---|---|---|---|
| TTS | `F5-TTS` | `comfy/.../tts/f5/` | 4 GB |
| TTS alt | `XTTS-v2` | (separate Coqui install) | 4 GB |
| Music | `Stable Audio Open` | `comfy/.../audio/sao/` | 6 GB |
| Music alt | `MusicGen-Large` | (HF transformers) | 6 GB |
| STT (captions) | `Whisper large-v3` | `comfy/.../stt/whisper/` | 3 GB |

## Cloud escalation (paid, optional)

| Modality | Provider | Cost | Use |
|---|---|---|---|
| Video gen | Runway Gen-3 | $0.50-2/clip | Premium quality, paying client |
| Video gen | Pika 2.0 | $0.30-1.50/clip | Stylized alt |
| Voice clone | ElevenLabs | $0.30/min | Hard clone case |
| Music | Suno / Udio | $0.10/song | Branded jingle |
| TTV (text-to-video) | Sora API / Veo | $$$$ | Future, very expensive |

## Frontier (Tier S text-only)

| Logical | Provider | Use |
|---|---|---|
| `planner-script-hard` | Claude Opus 4.7 | Narrative iterate, taste arbiter |
| `reviewer-frames` | Claude Opus 4.7 | Vision review 6 frames + transcript |
| `adjudicator` | Claude Opus 4.7 | Brand match tie-break |
| `architect` | Claude Opus 4.7 | Campaign concept |

## Update log

| Date | Change | Reason |
|---|---|---|
| 2026-06-09 | Initial inventory | Bootstrap |
