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
