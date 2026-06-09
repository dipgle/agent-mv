# Conventions — Video Pipeline

## Quy tắc vàng (rút từ PLAYBOOK)

### Visual gen
1. **Không text→video trực tiếp** với LTX/Wan — luôn `text → Flux keyframe → image→video`. Quality cao hơn nhiều, kiểm soát composition tốt hơn.
2. **Render proxy 720p khi iterate, 4K khi final** — tiết kiệm 5–10× thời gian loop reviewer.
3. **Visual gen ƯU TIÊN local** — cloud ($0.30-2/clip) chỉ khi paying client.

### Audio
1. **Voice trước, video sau** — biết length voiceover rồi mới định shot duration.
2. **Music BPM phải match cut** — Planner ghi BPM trong shotlist, ffmpeg cut on-beat.
3. **Whisper cho subs auto, KHÔNG cho script** — script viết tay (Planner), Whisper chỉ generate SRT từ voiceover đã có.

### Brand
1. **Brand JSON là single source of truth** — color, font, logo, voice tone, music mood. Mọi role đọc từ đây.
2. **Reviewer chỉ xem sample frames + transcript** — 6 frames @ 5s interval đủ check pacing.
3. **Brand match phải manual sample** — không có metric tự động đáng tin cho "đúng tinh thần brand".

### Hybrid (CLI/API)
1. **Local-first cascade text** (Planner/Reviewer) — escalate Claude khi cần taste.
2. **Cloud cho Planner script + Reviewer brand** — đây là chỗ Claude/GPT đáng tiền.
3. **CLI cho campaign concept + script iterate** — interactive feel.
4. **API cho batch render orchestration** — 12 shots × Reviewer = parallel call.
5. **Cache reference style guide** — brand JSON + storyboard refs qua Anthropic 90% discount.
6. **Spend cap riêng cho visual cloud** — Runway/Pika $20/video, tách khỏi text LLM cap.

### Evaluation
1. **Mỗi asset có row riêng trong `assets`** — không gộp 12 shot vào 1 row. Truy vết shot-level regression.
2. **Golden set per modality bất biến** — đặc biệt visual: 10 prompts cover composition variety.
3. **Render time là FIRST-CLASS metric** — không secondary. 5× faster với 80% quality vẫn có thể đáng đổi.
4. **Save preview low-res** — keep 480p mỗi shot vĩnh viễn để A/B sau, full 4K archive selective.
5. **Pacing eval phải watch full video** — không sample frame. Reviewer/Adjudicator xem 1 lần cuối trước approve.

### Privacy (Web chat farm)
1. **KHÔNG paste client brand asset, footage, voiceover vào free web chat.**
2. **Redaction guard cứng** — grep `client_name`, `brand/`, `assets/proprietary/` → block.

### Codex pool discipline (Tier S free key rotation)
1. **Codex pool ONLY cho Adjudicator + Architect role.** `cost_gate.assert_codex_quota_role()` reject mọi caller khác. Executor/Reviewer loop tuyệt đối không gọi pool — sẽ đốt quota trong 1 video.
2. **Cascade tự động**: pool exhausted → escalate sang `adjudicator-paid` (Claude Opus) → log decision sang devlog (không silent).
3. **Account hygiene**: throwaway accounts, dedicated IP (residential proxy nếu nhiều), không link payment/phone giữa các account. OpenAI ToS cấm multi-account quota stacking — risk ban cascade nếu fingerprint chung.
4. **Trial credit ≠ unlimited**: mỗi acc ~$5-18 → ~50-200 GPT-5-codex call. Pool 3 acc = ~500 total. Hết là phải tạo acc mới hoặc fallback paid.
5. **Primary free traffic qua Groq + Cerebras + Codestral + OpenRouter** (legit free, zero ToS risk, ~30K req/day combined). Pool chỉ là supplemental cho judgment task khi cần GPT-5 frontier quality.
6. **Monitoring qua devlog**: cost_gate log mỗi pool key swap + 429 event. Supervisor dashboard hiện pool usage tab.

## Project structure

### Folder
```
video-projects/<feature_id>/
├── reference.json           # Researcher output
├── style_guide.md
├── script.md                # Planner output
├── shotlist.json
├── brand.json               # SSOT
├── shots/
│   ├── 01_keyframe.png
│   ├── 01_clip.mp4
│   ├── 01_meta.json         # generation params
│   └── ...
├── voice.wav
├── bgm.wav
├── subs.srt
├── proxy/                   # 480p preview archive
│   └── shot_NN_v1.mp4
└── final.mp4
```

### Naming
- shot index zero-padded: `01_`, `02_`, ... `12_`
- asset type suffix: `_keyframe.png`, `_clip.mp4`, `_voice.wav`
- version suffix khi iterate: `_v1`, `_v2`
- Final: `final_<aspect>_<resolution>.mp4` ví dụ `final_9x16_1080p.mp4`

### Brand JSON schema
```json
{
  "name": "Brand",
  "colors": {
    "primary": "#0066ff",
    "secondary": "#fff",
    "accent": "#ff5500"
  },
  "fonts": {
    "title": "Inter Bold",
    "body": "Inter Regular",
    "caption": "JetBrains Mono"
  },
  "voice_tone": "friendly, confident, technical",
  "music_mood": ["uplifting", "modern", "minimal"],
  "music_bpm_range": [100, 130],
  "logo_path": "./assets/logo.svg",
  "logo_safe_area_pct": 5
}
```

## Output discipline

### Executor render
- Mỗi shot có `_meta.json` ghi: model, params (seed, steps, cfg, sampler), prompt, negative_prompt, duration, render_time, vram_peak.
- Log vào devlog `kind=artifact`, refId = path file.
- Preview 480p phải copy sang `proxy/` cùng version suffix.

### Reviewer critique — JSON structured
```json
{
  "verdict": "approved|rejected",
  "overall_score": 0-100,
  "issues": [
    {
      "shot": 5,
      "type": "pacing|audio_sync|brand|composition|motion",
      "severity": "critical|major|minor",
      "msg": "shot 5 dài 4.5s, vượt budget 3s, kéo dài hook"
    }
  ],
  "suggestions": ["rút shot 5 về 2.5s, dồn 2s sang outro"]
}
```

### Final QA checklist
- [ ] Audio loudness: -14 LUFS (YouTube) / -16 LUFS (streaming)
- [ ] Captions: WER <5% vs voiceover script
- [ ] Aspect ratio + resolution đúng spec
- [ ] Bitrate efficient (H.264 high profile, ~5 Mbps cho 1080p 30fps)
- [ ] Logo trong safe area mọi shot có brand element
- [ ] Hook (0-3s) có loop-back lý do
- [ ] CTA rõ ở 3s cuối
- [ ] File size <100MB cho 30s 1080p

## Tooling discipline

- **ComfyUI**: workflow JSON checked vào git tại `workflows/`. Không commit weights.
- **ffmpeg**: scripts ở `scripts/compose.sh` — không inline ffmpeg trong Python.
- **DaVinci**: chỉ manual polish bước cuối, KHÔNG dependency tự động.
- **Code comments in English** (memory: `feedback_code_comments_english`)

## License hygiene

Commercial production requires every model in the pipeline to have a license
that permits commercial use without attribution or revenue restrictions.

### Allowed licenses

| License | Notes |
|---|---|
| Apache 2.0 | Fully commercial OK; include NOTICE file if distributing model |
| MIT | Fully commercial OK |
| BSD 2-Clause / BSD 3-Clause | Fully commercial OK |
| CC0 (Public Domain) | No restrictions |
| CC-BY 4.0 | Commercial OK with attribution in credits |
| Pixabay License | Royalty-free, commercial OK, no attribution required |

### Blocked licenses (must NOT appear in production pipeline)

| License | Blocked model examples | Reason |
|---|---|---|
| CC-BY-NC (any variant) | Stable Audio Open, MusicGen-Large | Non-commercial only |
| BFL Non-Commercial | FLUX.1-dev | Black Forest Labs non-commercial |
| RAIL-Research / Research-only | LTX-Video (Lightricks) | Research use only |
| CreativeML OpenRAIL-M | SDXL base (some variants) | Requires output disclosure |

### Current production stack (all commercial-OK)

| Layer | Model | License |
|---|---|---|
| Image keyframe | FLUX.1-schnell | Apache 2.0 |
| Image-to-video | Wan2.1-T2V-14B | Apache 2.0 |
| Voice TTS | F5-TTS | Apache 2.0 |
| Music | Pixabay API + CC0 fallback | Pixabay License / CC0 |
| Captions | Whisper large-v3 | MIT |
| Text LLMs | Qwen3, DeepSeek-R1 | Apache 2.0 / MIT |

### COMMERCIAL_MODE env var

```
COMMERCIAL_MODE=1  # default — enforces Apache/MIT/CC0 cascade only
COMMERCIAL_MODE=0  # opt-in for personal/research — enables FLUX.1-dev + LTX-Video
```

`COMMERCIAL_MODE=0` **MUST NEVER** be used for client deliverables or any video
intended for commercial distribution.

### Process for adding a new model

1. Identify the exact license from the model's HuggingFace model card.
2. Check against the Allowed / Blocked table above.
3. In the PR description, include: model name, HuggingFace URL, license name,
   confirmation "Commercial use: YES / NO".
4. If commercial use is YES: add model to `infra/models.md` with License column.
5. If commercial use is NO: model can only be added under `COMMERCIAL_MODE=0`
   path in `cost_gate.py`, documented as personal/research only.
6. Never add a blocked-license model to `infra/setup.sh` / `infra/setup.ps1`
   as a default download.

## Compliance

Two layers enforce legal compliance on every produced video before publish.
Both are non-blocking when their optional dependencies are absent — they
degrade gracefully with a logged warning rather than crashing the pipeline.

### Layer A — Content moderation (`orchestrator/lib/moderation.py`)

Runs as **Tier 0**, before Tier 1 deterministic checks and before any LLM
panel cost is incurred.

| Check | Library | License | When it runs |
|-------|---------|---------|--------------|
| NSFW detection | NudeNet (`nudenet>=3.4.0`) | Apache 2.0 | Every reviewer pass |
| Real-person face | OpenCV Haar cascade (already in deps) | LGPL | Every reviewer pass |
| Trademark similarity | CLIP via `open-clip-torch>=2.24.0` | MIT | Every reviewer pass; skipped if `data/trademark_index/` is empty |
| Voice clone consent | Brand JSON field check (no extra dep) | — | Every reviewer pass |

**Severity levels:**
- `critical` → pipeline auto-rejects immediately; no LLM cost burned.
  Example: explicit exposed content detected by NudeNet.
- `major` → video is not blocked automatically; Reviewer panel is informed
  and the flag appears in `critique.json` + dashboard Compliance tab.
  Examples: face detected without consent confirmation, near-trademark match.
- `ok` → no issue; pipeline continues normally.

**Trademark index:** drop PNG/JPG logo files into `data/trademark_index/`.
The pipeline builds `trademark_embeddings.npz` on first run.  Empty index →
check is silently skipped.  See `data/trademark_index/README.md`.

### Layer B — C2PA Content Credentials (`orchestrator/lib/c2pa.py`)

Runs immediately after `ffmpeg` compose (inside `compose()`), before the
Reviewer sees the video.

Embeds a machine-readable AI-disclosure manifest into `final.mp4` containing:
- `c2pa.aiGenerated` action with `trainedAlgorithmicMedia` source type
- `c2pa.training-mining: notAllowed` (training opt-out)
- `agent-mv.pipeline` custom assertion with feature_id + model stack

**Signing:**
- Set `C2PA_SIGNING_KEY_PATH` + `C2PA_CERT_CHAIN_PATH` for signed credentials.
- Without env vars: unsigned Annotated Credentials (sufficient for most platforms).
- Dev cert: `python orchestrator/lib/c2pa.py gen-cert --out certs/dev`

**Inspect a video's credentials:**
```bash
c2patool out/VID-001/final.mp4        # official CLI
python -c "from orchestrator.lib.c2pa import verify; from pathlib import Path; print(verify(Path('out/VID-001/final.mp4')))"
```

Full documentation: `docs/c2pa.md`

**Dashboard:** eval/dashboard.html → Compliance tab shows:
- Moderation flags (table, severity-colored)
- C2PA embed status per video
- Trademark index size
