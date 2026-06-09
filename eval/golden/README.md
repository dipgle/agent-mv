# Golden Set — Video Pipeline

Fixed tasks per modality for benchmarking candidate models. **Bất biến** sau khi commit.

## Structure (per modality)

```
golden/
├── keyframe/
│   ├── 01_portrait.json      # prompt + expected CLIP-score min
│   ├── 02_landscape.json
│   ├── 03_abstract.json
│   ├── 04_product.json
│   ├── 05_text_heavy.json
│   └── ...
├── motion/
│   ├── 01_pan_left.json      # keyframe path + motion desc + expected
│   └── ...
├── voice/
│   ├── 01_neutral.json       # script + expected UTMOS min
│   ├── 02_energetic.json
│   ├── 03_calm.json
│   └── ...
├── music/
│   ├── 01_120bpm_upbeat.json # brief + expected BPM alignment
│   └── ...
├── captions/
│   ├── 01_clear_voice.json   # audio + ground truth SRT
│   └── ...
└── final/
    ├── 01_full_30s.json      # full pipeline reproduction tolerance
    └── ...
```

## Per-modality threshold

| Modality | Primary metric | Pass threshold |
|---|---|---|
| Keyframe | CLIP-score | ≥0.25 |
| Keyframe | Aesthetic score | ≥6.0 |
| Motion | CLIP-temporal | ≥0.85 |
| Motion | Flicker rate | ≤0.08 |
| Voice | UTMOS | ≥3.8 |
| Voice | WER round-trip | ≤0.05 |
| Music | BPM alignment | ±5 BPM của brief |
| Captions | WER | ≤0.05 |
| Final | Pacing variance | ≤1.5s |
| Final | Audio sync | ≤30ms offset |

## Smoke run

```bash
python eval/smoke_test.py --modality keyframe --model comfy/flux.1-dev \
    --golden eval/golden/keyframe/
```

Pass threshold: ≥80% golden tasks meet primary metric → eligible canary.
