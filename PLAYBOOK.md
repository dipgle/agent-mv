# Video Production Pipeline — Local Only

**Mục tiêu**: Dựng video (short, ad, explainer, vlog) end-to-end bằng model local 100%, không cloud. 4 roles song song với UIUX playbook, samples lấy từ web free.

## 1. Roles & Models

Video stack phức tạp hơn UIUX — chia model theo **modality** (text/image/video/audio):

| # | Vai | Modality | Model | VRAM | Lý do |
|---|---|---|---|---|---|
| 1 | **Researcher** (tìm + so sánh reference) | Text+Vision | `qwen2.5-vl:7b` + `qwen3:8b` | 10 GB | VL đọc thumbnail/storyboard, qwen3 reason transcript |
| 2 | **Planner** (script + storyboard + shot list) | Text | `qwen3:32b-thinking` HOẶC `deepseek-r1:14b` | 20 / 9 GB | Thinking-mode → 3-act structure, pacing, B-roll cue |
| 3a | **Executor — Keyframe** (text→image) | Image | `flux.1-dev` (Q4) hoặc `sdxl-turbo` | 12–24 GB | Flux best quality 2026, SDXL nhẹ |
| 3b | **Executor — Motion** (image→video) | Video | `LTX-Video` HOẶC `Wan2.1-14B` HOẶC `Hunyuan-Video` | 12–60 GB | LTX nhanh nhất, Wan/Hunyuan quality cao |
| 3c | **Executor — Voiceover** (text→speech) | Audio | `F5-TTS` HOẶC `XTTS-v2` HOẶC `Bark` | 4 GB | F5 zero-shot clone, XTTS multilingual |
| 3d | **Executor — Music/SFX** | Audio | `Stable Audio Open` + `MusicGen-Large` | 6 GB | Stable Audio nhạc nền, MusicGen có loop |
| 3e | **Executor — Caption** (auto subs) | Audio→Text | `Whisper large-v3` | 3 GB | SOTA STT free, multilingual |
| 3f | **Executor — Lip sync** (optional) | Video | `LatentSync` HOẶC `MuseTalk` HOẶC `SadTalker` | 8–16 GB | Khi cần talking head |
| 3g | **Executor — Upscale** | Video | `Real-ESRGAN` + `RIFE` (frame interp) | 4 GB | 720p → 4K, 24→60fps |
| 4 | **Reviewer** (pacing, audio sync, brand) | Text+Vision | `deepseek-r1:14b` + `qwen2.5-vl:7b` | 14 GB | R1 reason timing, VL check frame consistency |
| + | Compose | — | `ffmpeg` + `DaVinci Resolve` free | — | Final assembly |

## 2. Pull / Install

```bash
# Text models (Ollama)
ollama pull qwen2.5-vl:7b
ollama pull qwen3:8b
ollama pull qwen3:32b
ollama pull deepseek-r1:14b

# Image/Video/Audio models qua ComfyUI
git clone https://github.com/comfyanonymous/ComfyUI && cd ComfyUI
pip install -r requirements.txt

# Download model weights (HuggingFace)
huggingface-cli download black-forest-labs/FLUX.1-dev --local-dir models/checkpoints/flux
huggingface-cli download Lightricks/LTX-Video --local-dir models/checkpoints/ltx
huggingface-cli download SWivid/F5-TTS --local-dir models/tts/f5
huggingface-cli download stabilityai/stable-audio-open-1.0 --local-dir models/audio/sao
huggingface-cli download openai/whisper-large-v3 --local-dir models/stt/whisper

# Tools
brew install ffmpeg
# DaVinci Resolve free: blackmagicdesign.com/products/davinciresolve
```

## 3. Workflow

```
User intent ("video TikTok 30s giới thiệu app SaaS analytics")
   │
   ▼
[1 Researcher] scrape 5–8 reference videos từ YT/Vimeo/Runway
   │ → transcribe (Whisper) → analyze pacing/hooks/CTA
   │ → vision parse thumbnail + key frames
   │ (output: reference.json + style_guide.md)
   ▼
[2 Planner] reference.json + intent → script.md
   │   - 3-act structure (hook 0-3s, body 3-25s, CTA 25-30s)
   │   - shot list (8–12 shots, mỗi shot 2-4s)
   │   - voiceover lines per shot
   │   - music brief (BPM, mood, key)
   │   - text overlay timing
   │ (output: script.md + shotlist.json)
   ▼
[3 Executor pipeline song song]
   ├─ [3a Keyframe] Flux → 1 image/shot (12 images)
   ├─ [3b Motion]   LTX-Video → 12 clip 2-4s
   ├─ [3c Voice]    F5-TTS → voiceover.wav
   ├─ [3d Music]    Stable Audio → bgm.wav (30s loop)
   ├─ [3e Caption]  Whisper → subs.srt từ voiceover
   └─ [3g Upscale]  Real-ESRGAN → 1080p
   │
   ▼
[ffmpeg Compose] ghép clips + voiceover + bgm + subs → final.mp4
   │
   ▼
[4 Reviewer] xem final.mp4 (sample frames qua VL + transcript qua R1)
   │   - pacing OK? (mỗi shot ≥2s, ≤4s)
   │   - audio sync OK? (voice không lệch frame)
   │   - brand consistent? (color, font, logo)
   │   - hook strong? (3s đầu có loop-back lý do)
   │   - CTA rõ?
   │
   ├─ approved → DONE
   └─ rejected → loop về [3] shot cụ thể với critique.json (max 3 lần)
```

## 4. Nguồn mẫu free

**Video AI generators** (reference + clone style):
- runwayml.com/explore — Gen-3 gallery free view
- pika.art/explore — community feed
- lumalabs.ai/dream-machine — gallery
- klingai.com — gallery
- haiper.ai — gallery

**Stock footage free** (B-roll, transitions):
- pexels.com/videos
- pixabay.com/videos
- mixkit.co — premium free
- coverr.co
- videvo.net

**Music/SFX free**:
- freesound.org — SFX khổng lồ
- pixabay.com/music — royalty-free
- incompetech.com — Kevin MacLeod CC
- youtube.com/audiolibrary — YT free
- ccmixter.org

**Style/pacing refs**:
- vimeo.com/staffpicks — quality cinematography
- youtube.com (filter by upload date + region) — trends
- tiktok.com (creative center) — viral hooks
- behance.net (motion category)
- artofthetitle.com — title design

**Color grade LUT free**:
- freshluts.com
- rocketstock.com/free-after-effects-templates

## 5. Hardware constraint (Mac M3 Max 64GB / RTX 4090)

Video gen là **VRAM hog** — không chạy song song nhiều generator.

**Realistic timing trên M3 Max 64GB**:
| Task | Model | Thời gian | VRAM peak |
|---|---|---|---|
| Image 1024² | Flux.1-dev Q4 | ~25s | 18 GB |
| Video 5s 768² | LTX-Video | ~3 min | 22 GB |
| Video 5s 1280² | Wan2.1-14B | ~12 min | 40 GB |
| Voice 30s | F5-TTS | ~8s | 4 GB |
| Music 30s | Stable Audio | ~25s | 6 GB |
| Whisper 30s | large-v3 | ~5s | 3 GB |
| Upscale 1080p→4K | Real-ESRGAN | ~30s/min | 4 GB |

**Strategy A — Sequential pipeline** (default, an toàn):
ComfyUI workflow chạy 1 stage/lần, mỗi stage unload model trước.

**Strategy B — Modal separation** (recommended):
- Text models (Ollama): always loaded, share 30 GB
- Visual generator: 1 active (Flux HOẶC LTX, swap by ComfyUI)
- Audio: F5 + Stable Audio nhẹ → cohabit

**Strategy C — Batch overnight**:
- Day: Planner + Researcher (fast)
- Night: Executor batch render 12 shots × video gen (slow)

## 6. Orchestrator

| Tool | Khi nào dùng |
|---|---|
| **ComfyUI** | Image+Video+Audio gen, node-based workflow |
| **CrewAI** | Multi-agent role orchestration (Researcher/Planner/Reviewer) |
| **ffmpeg script** | Final compose (concat, overlay, audio mix, subs burn) |
| **DaVinci Resolve free** | Manual polish nếu Reviewer reject |
| **n8n** | No-code orchestrate pipeline |

Đề xuất: **CrewAI gọi ComfyUI API + ffmpeg post**.

```python
# Pseudo-orchestrator
researcher.scrape_refs() → reference.json
planner.write_script(reference.json) → script.md + shotlist.json

for shot in shotlist:
    comfyui.run("flux_workflow", prompt=shot.image_prompt) → img.png
    comfyui.run("ltx_workflow", image=img.png, motion=shot.motion) → clip.mp4

f5_tts.synthesize(script.voiceover) → voice.wav
stable_audio.gen(script.music_brief) → bgm.wav
whisper.transcribe(voice.wav) → subs.srt
ffmpeg.compose(clips, voice, bgm, subs) → final.mp4

reviewer.evaluate(final.mp4) → critique.json | DONE
```

## 7. Quick start

```bash
# 1. Pull text models
bash setup-text.sh

# 2. Download ComfyUI weights (one-time, ~60 GB)
bash setup-comfy.sh

# 3. Boot services
ollama serve &
python ComfyUI/main.py --listen &

# 4. Run pipeline
python pipeline.py --intent "TikTok 30s SaaS analytics intro" \
                   --aspect 9:16 \
                   --duration 30 \
                   --voice clone:./samples/founder.wav \
                   --brand ./brand/style.json

# Output: ./out/<timestamp>/
#   ├─ reference.json
#   ├─ script.md
#   ├─ shotlist.json
#   ├─ shots/
#   │   ├─ 01_keyframe.png
#   │   ├─ 01_clip.mp4
#   │   └─ ...
#   ├─ voice.wav
#   ├─ bgm.wav
#   ├─ subs.srt
#   └─ final.mp4
```

## 8. Quy tắc vàng

1. **Không text→video trực tiếp với LTX/Wan** — luôn `text → Flux keyframe → image→video`. Quality cao hơn nhiều, kiểm soát composition tốt hơn.
2. **Voice trước, video sau** — biết length voiceover rồi mới định shot duration. Tránh re-render video khi voice đổi.
3. **Music BPM phải match cut** — Planner ghi BPM trong shotlist, ffmpeg cut on-beat.
4. **Whisper cho subs auto, KHÔNG cho script** — script viết tay (Planner), Whisper chỉ generate SRT từ voiceover đã có.
5. **Reviewer chỉ xem sample frames + transcript** — không cần load full video vào model (quá đắt). 6 frames @ 5s interval đủ check pacing.
6. **Render proxy 720p khi iterate, 4K khi final** — tiết kiệm 5–10× thời gian loop reviewer.
7. **Brand JSON là single source of truth** — color, font, logo, voice tone, music mood. Mọi role đọc từ đây.

## 9. Hybrid extension — thêm cloud model (Claude / Codex / GPT)

**Quan trọng**: cloud có thể gọi qua **CLI hoặc API đều được**, mix tự do. Cho video, cloud lợi thế lớn ở **Planner (script taste) + Reviewer (judgement)**, KHÔNG thay được visual gen (Runway/Pika API đắt + ratelimit).

### 9.1 CLI mode
| Tool | Backend | Vai phù hợp trong video pipeline |
|---|---|---|
| `claude` (Claude Code) | Anthropic | Architect (campaign concept), Planner script, Adjudicator brand check |
| `codex` (Codex CLI) | OpenAI | Bash/ffmpeg/ComfyUI workflow scripting, batch render automation |
| `gemini` CLI | Google | Long-context reference parse (50+ video transcripts 1 shot) |

### 9.2 API mode
| SDK | Vai phù hợp |
|---|---|
| `anthropic` SDK | Reviewer batch (12 shots × structured JSON critique) |
| `openai` SDK | Researcher scraping/transcribing parallel |
| **LiteLLM proxy** | Unified gateway, route local Ollama / Claude / GPT |
| Runway/Pika API (cloud video gen) | Tier S Executor escalation — ĐẮT $$$$, chỉ khi LTX/Wan fail quality |

### 9.3 Map role → CLI/API/Local
| Role | Default (local) | Cloud escalation | Channel khuyến nghị |
|---|---|---|---|
| Researcher transcribe refs | Whisper local | Gemini Flash (vol cao) | **API** (parallel) |
| Researcher vision parse | qwen-vl:7b | Claude Opus vision | **API** |
| Planner script + storyboard | qwen3:32b | Claude Opus (narrative taste) | **CLI** ưu thế (interactive iterate) |
| Executor — keyframe (Flux) | Local ComfyUI | (skip cloud) | Local only |
| Executor — motion (LTX/Wan) | Local ComfyUI | Runway Gen-3 (chỉ khi cần SOTA) | API nếu escalate |
| Executor — voice (F5-TTS) | Local | ElevenLabs (clone hard) | API nếu escalate |
| Executor — music (Stable Audio) | Local | Suno/Udio API | API nếu escalate |
| Executor — caption (Whisper) | Local | (skip) | Local only |
| Reviewer pacing+audio sync | R1 + VL | Claude Opus (6 frames + transcript) | **API** (JSON output) |
| Adjudicator brand/taste | — | Claude Opus only | **CLI** (manual judgment) |
| Architect (campaign concept) | — | Claude Opus only | **CLI** |

### 9.4 Cascade visual gen (đặc thù video)
Visual gen escalation **KHÔNG default** vì cost gap quá lớn:
```
LTX-Video local (free, 3 min/clip)
   │
   ├─ quality OK → DONE
   └─ fail 3× → CHỌN:
        a) Wan2.1-14B local (12 min/clip, vẫn free)
        b) Runway Gen-3 API ($0.50–2.00/clip)
        c) Pika 2.0 API ($0.30–1.50/clip)
```
→ Default = (a) Wan local. Cloud (b)(c) chỉ khi client trả tiền premium.

### 9.5 LiteLLM proxy cho video
```yaml
model_list:
  - model_name: planner
    litellm_params:
      model: ollama/qwen3:32b
      api_base: http://localhost:11434
  - model_name: planner-script-hard
    litellm_params:
      model: anthropic/claude-opus-4-7
  - model_name: reviewer-frames
    litellm_params:
      model: anthropic/claude-opus-4-7  # vision
  - model_name: researcher-bulk
    litellm_params:
      model: gemini/gemini-3-flash
litellm_settings:
  max_budget: 100         # video tốn hơn UIUX → cap cao hơn
  cache: true
```

### 9.6 Quy tắc vàng hybrid (video)
1. **Visual gen ƯU TIÊN local** — cloud video gen ($0.30–2/clip) đắt + ratelimit, chỉ escalate khi client paying.
2. **Cloud cho Planner script + Reviewer brand** — đây mới là chỗ Claude/GPT đáng tiền (taste + judgment).
3. **CLI cho campaign concept + script iterate** — interactive feel quan trọng cho creative work.
4. **API cho batch render orchestration** — 12 shots × Reviewer = parallel call, CLI không phù hợp.
5. **Cache reference style guide** — brand JSON + storyboard refs cache qua Anthropic 90% discount.
6. **Spend cap riêng cho visual cloud** — Runway/Pika cap riêng $20/video, tách khỏi text LLM cap.

## 10. So sánh với UIUX playbook

| Khía cạnh | UIUX | Video |
|---|---|---|
| Modality | Text-only | Text + Image + Video + Audio |
| Tool chính | Continue.dev + Ollama | ComfyUI + Ollama + ffmpeg |
| Output | Code (HTML/JSX/CSS) | Binary (mp4, wav, png) |
| Iteration speed | Giây | Phút–giờ |
| VRAM peak | 30 GB | 40–60 GB |
| Reviewer loop | Cheap (re-render JSX) | Đắt (re-render video) |
| Sequential vs parallel | Parallel-friendly | Bottleneck ở visual gen |
| Free sample sources | Code/component libs | Stock video + AI galleries |

Cùng pattern 4-roles, khác ở **chi phí mỗi loop** → Video cần plan kỹ hơn ở vai 2 (Planner) để giảm số lần loop ở vai 4 (Reviewer).

## 11. Model Evaluation & Continuous Improvement

Mục tiêu: track từng model (text + visual + audio) qua mỗi render → so sánh alt → swap khi đo được tốt hơn → video output cải thiện theo thời gian. Video đặc thù phải tách metric **per modality**.

### 11.1 Metrics theo role (video-specific)

| Role | Modality | Primary metric | Secondary | Cách đo |
|---|---|---|---|---|
| Researcher transcribe | Audio→Text | Transcript accuracy (WER vs ground truth) | latency, cost | Whisper WER eval |
| Researcher vision | Vision | Pattern usefulness 1-5 (Planner chấm) | latency, cost | Field trong reference.json |
| Planner script | Text | Reviewer first-pass approval %, hook strength 1-5 | spec completeness, latency | Reviewer field |
| Executor — keyframe | Image | CLIP-score vs prompt, aesthetic score (LAION-Aesthetic) | render time, VRAM peak | Auto eval per image |
| Executor — motion | Video | Frame consistency (CLIP temporal), motion-smoothness, flicker rate | render time | VBench subset |
| Executor — voice | Audio | MOS (Mean Opinion Score) 1-5, WER round-trip, prosody | RTF (real-time factor) | UTMOS auto + manual sample |
| Executor — music | Audio | Beat alignment to script BPM, brand-mood match 1-5 | gen time | Beat tracker + reviewer |
| Executor — caption | STT | WER, timing offset (ms) | latency | Compare vs voiceover script |
| Reviewer pacing | Multi | True-positive pacing issues, agreement w/ Adjudicator | latency, cost | Manual audit weekly |
| Adjudicator brand | Multi | Override rate, time-to-decision | — | Log mỗi tie-break |

### 11.2 Storage — SQLite `./eval.sqlite`

Schema giống UIUX playbook 11.2 nhưng `metrics_json` chứa field video-specific:
```sql
-- Executor keyframe row
{"clip_score": 0.31, "aesthetic": 6.2, "vram_peak_gb": 18, "render_s": 25}

-- Executor motion row
{"clip_temporal": 0.89, "flicker_rate": 0.04, "render_s": 180, "vram_peak_gb": 22}

-- Executor voice row
{"utmos": 4.1, "wer_roundtrip": 0.03, "rtf": 0.12}

-- Final video row
{"audio_sync_ms": 18, "pacing_variance_s": 0.7, "brand_match": 4.5, "lighthouse_a11y": null}
```

Thêm bảng `assets`:
```sql
CREATE TABLE assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER REFERENCES runs(id),
  feature_id TEXT,           -- video project
  shot_idx INTEGER,
  asset_type TEXT,           -- keyframe|clip|voice|music|caption|final
  path TEXT,
  duration_s REAL,
  size_bytes INTEGER,
  quality_json TEXT          -- per-asset eval scores
);
```

→ Truy vết được "shot #5 keyframe Flux dev Q4 vs Q8 chất lượng diff bao nhiêu".

### 11.3 Cadence

| Cadence | Action |
|---|---|
| Per-render | Auto write `runs` + `assets` (ComfyUI hook + wrapper) |
| Daily | Roll-up score per (role, model, modality) |
| Weekly | Scorecard + audit 3 random renders (manual review brand match) |
| Monthly | A/B new visual gen release: LTX → LTX-2, Wan2.1 → 2.5, Flux dev → kontext, F5 → next gen |
| Quarterly | Re-eval cloud Runway/Pika pricing vs local quality gap; renegotiate |

### 11.4 Swap criteria (video-specific)

Tier năng lực khác nhau:
- **Text models** (planner/reviewer): giống UIUX — ≥15% pass rate gain, no >5% regression
- **Visual gen** (Flux/LTX/Wan): cao hơn — ≥20% CLIP-score gain HOẶC ≥30% render-time gain at same quality (render time cực đắt)
- **Audio gen** (F5/Stable Audio): ≥10% MOS gain hoặc ≥30% RTF gain
- **Lipsync/upscale**: pixel-level diff metric (LPIPS/SSIM) ≥10% better

Canary visual gen: render 5 shot golden set → manual A/B blind score → quyết định.

### 11.5 Golden set per modality

`./eval/golden/`:
```
keyframe/      → 10 prompts (portrait, landscape, abstract, product, text-heavy, ...)
motion/        → 10 keyframe-input + motion descriptors
voice/         → 10 script lines × 3 emotions
music/         → 10 brief (BPM, mood, key, duration)
captions/      → 10 audio samples + ground truth SRT
final/         → 3 full 30s videos (must reproduce within tolerance)
```

Bất biến. Test mọi candidate trên golden trước canary.

### 11.6 A/B routing (visual gen — ComfyUI workflow level)

Khác text model (LiteLLM route), visual gen split traffic ở **workflow level**:
```python
# orchestrator/router.py
def gen_motion(keyframe, motion_desc):
    if random() < 0.20:                            # 20% canary
        wf = "ltx_v2_workflow.json"
        model_tag = "ltx-video-2"
    else:                                          # 80% champion
        wf = "ltx_v1_workflow.json"
        model_tag = "ltx-video-1"
    out = comfyui.run(wf, image=keyframe, prompt=motion_desc)
    db.append_run(role="executor-motion", model=model_tag,
                  metrics_json=eval_clip_temporal(out))
    return out
```

### 11.7 Auto-discovery (video stack)

Weekly cron monitor:
- HuggingFace trending: `text-to-video`, `image-to-video`, `text-to-audio`, `tts`
- ComfyUI Manager new node releases
- Anthropic/OpenAI/Google video model releases (Sora API, Veo, ...)
- Civitai LoRA trending (Flux LoRA cho brand style)

Auto-pull weight + run golden set → flag candidate. Manual approve trước canary vì weight ~5-60 GB/model, không pull bừa.

### 11.8 Symptom → Swap action (video)

| Triệu chứng | Hành động |
|---|---|
| Keyframe CLIP-score < 0.25 | Thử Flux Pro variant; thêm LoRA brand-specific; escalate Gemini imagen API |
| Motion flicker rate >0.1 | LTX → Wan2.1; tăng motion smoothness param; thêm RIFE frame interp |
| Voice MOS <3.5 | F5 → XTTS-v2; thử ElevenLabs API cho high-stake; train voice clone từ founder sample |
| Music không match BPM | MusicGen → Stable Audio Open; chỉ định BPM trong prompt rõ hơn |
| Caption WER >5% | Whisper large-v3 → whisper.cpp với VAD pre-filter; thêm domain prompt |
| Final pacing variance >2s | Planner thêm shot-buffer; switch sang Claude Opus planner |
| Render time/shot >5min | Lower resolution canary; thử LTX-Video (nhanh nhất); xét cloud nếu paying client |
| Brand match score <4 | Thêm reference image vào prompt; train LoRA; escalate Adjudicator Opus |

### 11.9 Output quality tracking — sản phẩm cuối

Đo chất lượng **video output** thực tế:
- Watch-time % (nếu có analytics YouTube/TikTok)
- Engagement: like/view, comment sentiment
- Brand survey score (sample audience)
- Technical: bitrate efficiency, file size vs duration, color gamut coverage
- Audio loudness compliance (-14 LUFS YouTube, -16 streaming)

Track theo `feature_id` → join với `runs.model` per modality → biết visual stack nào (Flux + LTX) đẻ ra video watch-time cao nhất.

### 11.10 Dashboard `./eval/dashboard.html`

Vanilla JS, no framework (giống UIUX). Tabs per modality:
- **Text**: planner/reviewer leaderboard, agreement matrix
- **Image**: CLIP-score scatter (model × prompt category)
- **Video**: flicker rate trend, render time/shot p50/p95
- **Audio**: MOS distribution per voice model
- **Final**: watch-time vs render cost scatter
- **Swap queue**: canary status + golden set scores

### 11.11 Quy tắc vàng evaluation (video)

1. **Mỗi asset có row riêng trong `assets`** — không gộp 12 shot vào 1 row. Truy vết được shot-level regression.
2. **Golden set per modality bất biến** — đặc biệt visual: 10 prompts cover composition variety, không sửa khi model mới fail.
3. **Render time là FIRST-CLASS metric** — không phải secondary. Visual gen 5x faster với 80% quality vẫn có thể đáng đổi (iteration loop nhanh hơn).
4. **Brand match phải manual sample** — không có metric tự động đáng tin cho "đúng tinh thần brand".
5. **Cloud video gen cap riêng** — tách Runway/Pika budget khỏi text LLM budget; cap $20/video.
6. **Save preview low-res** — keep 480p version mỗi shot vĩnh viễn để A/B sau, full 4K archive selective.
7. **Pacing eval phải watch full video** — không sample frame. Reviewer/Adjudicator phải xem 1 lần cuối trước approve.
