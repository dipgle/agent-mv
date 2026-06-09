# Architecture — Video Production Multi-Agent Pipeline

Stack 4 roles, local-first, modality-split executor. Tham khảo [PLAYBOOK.md](../PLAYBOOK.md) cho overview ngắn.

## 1. Roles & Models

Video stack chia executor theo **modality** (text/image/video/audio):

| # | Vai | Modality | Model | VRAM | Lý do |
|---|---|---|---|---|---|
| 1 | **Researcher** | Text+Vision | `qwen2.5-vl:7b` + `qwen3:8b` | 10 GB | VL parse thumbnail, qwen3 reason transcript |
| 2 | **Planner** | Text | `qwen3:32b-thinking` HOẶC `deepseek-r1:14b` | 20 / 9 GB | 3-act structure, pacing, B-roll cue |
| 3a | **Executor — Keyframe** (text→image) | Image | `flux.1-dev` (Q4) hoặc `sdxl-turbo` | 12–24 GB | Flux best quality 2026 |
| 3b | **Executor — Motion** (image→video) | Video | `LTX-Video` / `Wan2.1-14B` / `Hunyuan` | 12–60 GB | LTX nhanh, Wan/Hunyuan quality |
| 3c | **Executor — Voiceover** | Audio | `F5-TTS` / `XTTS-v2` / `Bark` | 4 GB | F5 zero-shot clone |
| 3d | **Executor — Music/SFX** | Audio | `Stable Audio Open` + `MusicGen-Large` | 6 GB | Stable Audio nhạc nền |
| 3e | **Executor — Caption** (auto subs) | Audio→Text | `Whisper large-v3` | 3 GB | SOTA STT free |
| 3f | **Executor — Lip sync** (optional) | Video | `LatentSync` / `MuseTalk` / `SadTalker` | 8–16 GB | Talking head |
| 3g | **Executor — Upscale** | Video | `Real-ESRGAN` + `RIFE` | 4 GB | 720p→4K, 24→60fps |
| 4 | **Reviewer** | Text+Vision | `deepseek-r1:14b` + `qwen2.5-vl:7b` | 14 GB | R1 timing reasoning, VL frame check |
| + | Compose | — | `ffmpeg` + `DaVinci Resolve` free | — | Final assembly |

## 2. Tier năng lực (cascade hybrid)

| Tier | Cho text role | Cho visual gen |
|---|---|---|
| **S — Frontier Cloud** | Claude Opus / GPT-5 (Planner script, Adjudicator brand) | Sora / Veo (chỉ paying client) |
| **A — Strong Cloud** | Claude Sonnet (Reviewer escalation) | Runway Gen-3 ($0.50-2/clip) |
| **A− — Free API** | Groq/Cerebras (bulk Researcher) | (không có free video gen API ổn định) |
| **B — Local Frontier** | qwen3-coder:30b, r1, qwen3:32b | Flux, LTX, Wan, Hunyuan, F5 |
| **C — Local Fast** | qwen3:8b, VL:7b, Whisper | Stable Audio Open |
| **W — Web chat farm** | Adjudicator brand vote | (không) |

## 3. Workflow

```
User intent ("video TikTok 30s giới thiệu SaaS analytics")
   │
   ▼
[1 Researcher] scrape 5–8 reference videos
   │ → Whisper transcribe → pacing/hook/CTA analysis
   │ → VL parse thumbnails
   │ (output: reference.json + style_guide.md)
   ▼
[2 Planner] reference + intent → script.md
   │   - 3-act: hook 0-3s, body 3-25s, CTA 25-30s
   │   - shot list 8–12 shots × 2-4s
   │   - voiceover lines per shot
   │   - music brief (BPM, mood, key)
   │   - text overlay timing
   │ (output: script.md + shotlist.json)
   ▼
[3 Executor pipeline song song]
   ├─ [3a Keyframe] Flux → 1 image/shot
   ├─ [3b Motion]   LTX → 12 clip 2-4s
   ├─ [3c Voice]    F5-TTS → voiceover.wav
   ├─ [3d Music]    Stable Audio → bgm.wav
   ├─ [3e Caption]  Whisper → subs.srt từ voiceover
   └─ [3g Upscale]  Real-ESRGAN → 1080p
   │
   ▼
[ffmpeg Compose] ghép clips + voiceover + bgm + subs → final.mp4
   │
   ▼
[4 Reviewer] xem final.mp4 (sample frames qua VL + transcript qua R1)
   │
   ├─ approved → DONE
   └─ rejected → loop về [3] shot cụ thể với critique.json (max 3 lần)
```

## 4. Cascade visual gen (đặc thù)

KHÁC text cascade — cost gap quá lớn nên KHÔNG auto escalate cloud:
```
LTX-Video local (free, 3 min/clip)
   │
   ├─ quality OK → DONE
   └─ fail 3× → CHỌN:
        a) Wan2.1-14B local (12 min/clip, vẫn free)
        b) Runway Gen-3 API ($0.50-2/clip)
        c) Pika 2.0 API ($0.30-1.50/clip)
```
→ Default = (a) Wan local. Cloud (b)(c) chỉ khi client paying premium.

## 5. Map role → CLI/API/Local

| Role | Default | Escalation | Channel |
|---|---|---|---|
| Researcher transcribe | Whisper local | Gemini Flash | API (parallel) |
| Researcher vision | qwen-vl:7b | Claude Opus vision | API |
| Planner script | qwen3:32b | Claude Opus (narrative taste) | CLI (interactive iterate) |
| Executor keyframe | Flux | (skip cloud) | Local only |
| Executor motion | LTX/Wan | Runway/Pika nếu paying | API nếu escalate |
| Executor voice | F5-TTS | ElevenLabs (clone hard) | API nếu escalate |
| Executor music | Stable Audio | Suno/Udio | API nếu escalate |
| Executor caption | Whisper | (skip) | Local only |
| Reviewer pacing | R1 + VL | Claude Opus (6 frames + transcript) | API (JSON) |
| Adjudicator brand | — | Claude Opus only | CLI (manual) |
| Architect campaign | — | Claude Opus only | CLI |

## 6. Hardware (Mac M3 Max 64GB / RTX 4090)

Realistic timing:
| Task | Model | Thời gian | VRAM peak |
|---|---|---|---|
| Image 1024² | Flux.1-dev Q4 | ~25s | 18 GB |
| Video 5s 768² | LTX-Video | ~3 min | 22 GB |
| Video 5s 1280² | Wan2.1-14B | ~12 min | 40 GB |
| Voice 30s | F5-TTS | ~8s | 4 GB |
| Music 30s | Stable Audio | ~25s | 6 GB |
| Whisper 30s | large-v3 | ~5s | 3 GB |
| Upscale 1080p→4K | Real-ESRGAN | ~30s/min | 4 GB |

Strategy: ComfyUI sequential pipeline + audio cohabit + text models always loaded.

## 7. Nguồn mẫu free

- **Video AI gallery** (reference): runwayml.com/explore, pika.art/explore, lumalabs.ai, klingai.com, haiper.ai
- **Stock footage**: pexels.com/videos, pixabay.com/videos, mixkit.co, coverr.co, videvo.net
- **Music/SFX**: freesound.org, pixabay.com/music, incompetech.com, youtube.com/audiolibrary, ccmixter.org
- **Style/pacing refs**: vimeo.com/staffpicks, youtube.com, tiktok.com/creative-center, behance.net, artofthetitle.com
- **Color LUT**: freshluts.com, rocketstock.com

## 8. Orchestrator stack

| Layer | Tool | Config path |
|---|---|---|
| Visual gen | ComfyUI | `infra/comfy/` + `workflows/` |
| Text orchestration | CrewAI + LangGraph | `orchestrator/` |
| Router | LiteLLM proxy | `infra/litellm.yaml` |
| Audio compose | ffmpeg scripts | `scripts/compose.sh` |
| Manual polish | DaVinci Resolve free | (external) |

## 9. Evaluation pipeline

Mọi asset (keyframe, clip, voice, music, caption) log vào `logs/devlog.sqlite` events + VIEW `model_runs`/`asset_quality` (xem `eval/schema.sql`).

Metric per modality:
- **Image**: CLIP-score, aesthetic
- **Video**: CLIP-temporal, flicker rate, render time
- **Audio**: UTMOS, WER round-trip, RTF
- **Final**: audio sync ms, pacing variance, brand match

## 10. Web chat farm (optional, dùng cho Adjudicator brand vote)

Vai trò nhỏ hơn UIUX vì video không có "code reviewer" tự động. Brand vote bằng 3 web chat (Claude/GPT/Gemini) thay vì gọi Opus paid.

## 11. Phase rollout

| Phase | Scope | Effort |
|---|---|---|
| 0 | Ollama + ComfyUI install + Flux + LTX + F5 + Whisper | 2 ngày |
| 1 | Text orchestrator (CrewAI Planner + Reviewer) + LiteLLM | 2 ngày |
| 2 | ComfyUI workflow JSON cho 6 modality | 3 ngày |
| 3 | ffmpeg compose script + smoke test e2e | 2 ngày |
| 4 | Eval golden set per modality + dashboard | 3 ngày |
| 5 | Web chat router (Adjudicator brand vote) | 2 ngày |
