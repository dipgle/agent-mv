# Video Production Pipeline — Local-First Multi-Agent

Tự động hoá sản xuất video (TikTok / Reels / YouTube short / explainer) bằng AI local: kịch bản, hình ảnh, video, voiceover, nhạc, caption, dựng phim.

Pipeline 4 vai: **Researcher → Planner → Executor (6 modality) → Reviewer**. Local-first qua Ollama + ComfyUI; escalation cloud (Claude/GPT) tùy chọn.

## Quick start

| OS | Hướng dẫn |
|---|---|
| 🪟 Windows 10/11 | [docs/INSTALL-WIN.md](docs/INSTALL-WIN.md) |
| 🍎 macOS (M-series) | [docs/INSTALL-MAC.md](docs/INSTALL-MAC.md) |
| 🐧 Linux (Ubuntu/Debian) | [docs/INSTALL-LINUX.md](docs/INSTALL-LINUX.md) |

Tóm tắt 5 bước (sau khi cài xong):
```bash
ollama serve &                                            # 1. text models
cd infra/comfy/ComfyUI && python main.py --listen &        # 2. visual gen
litellm --config infra/litellm.yaml --port 4000 &          # 3. router
python orchestrator/pipeline.py \
    --intent "TikTok 30s SaaS analytics intro" \
    --feature-id VID-001 \
    --brand brand-example.json                              # 4. render
open out/VID-001/final.mp4                                 # 5. xem
```

## Yêu cầu hệ thống

- **GPU**: NVIDIA 12GB+ VRAM (RTX 3060/4070/4090) HOẶC Apple M-series 32GB+ unified
- **RAM**: 32GB minimum, 64GB recommended
- **Storage**: 200GB SSD free (model weights ~60GB + render output)
- **OS**: Windows 10/11, macOS 14+, Ubuntu 22.04+

Chi tiết: [docs/INSTALL-*.md](docs/)

## Cấu trúc dự án

```
video/
├── README.md                ← bạn đang đọc
├── HANDOFF.md               ← context handoff cho dev/AI mới
├── PLAYBOOK.md              ← overview design quick read
├── CLAUDE.md                ← rules cho AI assistant (init-project template)
├── PLAN.md                  ← active goals
├── TODO.md                  ← actionable checklist
├── requirements.txt         ← Python deps
├── .env.example             ← env vars template
├── brand-example.json       ← brand spec sample
├── brand.json.template      ← brand spec skeleton
├── docs/
│   ├── architecture.md      ← 4 roles, tier system, workflow
│   ├── conventions.md       ← quy tắc vàng
│   ├── INSTALL-{WIN,MAC,LINUX}.md
│   ├── use-cases.md
│   ├── test-cases.md
│   └── decision-log.md
├── infra/
│   ├── setup.sh             ← Mac/Linux bootstrap
│   ├── setup.ps1            ← Windows bootstrap
│   ├── litellm.yaml         ← model routing config
│   └── models.md            ← model inventory + role mapping
├── orchestrator/
│   ├── pipeline.py          ← entry point 4 roles
│   └── lib/
│       ├── devlog.py        ← sqlite event logging
│       ├── litellm_client.py ← LLM wrapper (OpenAI-compat → :4000)
│       └── comfy_client.py  ← ComfyUI HTTP API client
├── workflows/               ← ComfyUI workflow JSON per modality
│   ├── README.md
│   ├── flux_keyframe.json.stub
│   ├── ltx_motion.json.stub
│   ├── f5_tts.json.stub
│   ├── stable_audio_music.json.stub
│   └── whisper_caption.json.stub
├── scripts/
│   ├── compose.sh           ← ffmpeg final compose (Linux/Mac)
│   └── compose.ps1          ← Windows port
├── eval/
│   ├── schema.sql           ← devlog VIEW extensions
│   ├── golden/              ← fixed benchmark tasks
│   └── dashboard.html       ← vanilla JS eval dashboard
├── logs/
│   └── devlog.sqlite        ← source of truth (events, UCs, TCs, runs)
└── memory/
    ├── active-context.md
    ├── session-summary.md
    └── discovered-knowledge.md
```

## Stack

| Layer | Tool | Vai trò |
|---|---|---|
| Text LLM | Ollama (`qwen3-coder:30b`, `deepseek-r1:14b`, `qwen3:32b`, `qwen2.5-vl:7b`) | Planner, Reviewer, Researcher |
| Image gen | ComfyUI + Flux.1-dev | Keyframe per shot |
| Video gen | ComfyUI + LTX-Video / Wan2.1 | Image-to-video motion |
| Voice TTS | ComfyUI + F5-TTS | Voiceover (zero-shot clone) |
| Music gen | ComfyUI + Stable Audio Open | BGM |
| STT | ComfyUI + Whisper large-v3 | Auto captions |
| Router | LiteLLM proxy | Local → free cloud → paid cascade |
| Orchestrator | CrewAI + LangGraph | 4-role pipeline |
| Compose | ffmpeg + DaVinci Resolve | Final assembly |
| Eval | SQLite + vanilla JS dashboard | Continuous improvement |

## Workflow

```
Intent
  ↓
[1 Researcher] reference.json
  ↓
[2 Planner] script.md + shotlist.json
  ↓
[3 Executor] keyframe → motion → voice + music + captions
  ↓
[ffmpeg] compose final.mp4
  ↓
[4 Reviewer] critique.json
  │
  ├─ approved → DONE
  └─ rejected → loop về [3] (max 3x) → Adjudicator (Claude Opus)
```

Chi tiết: [docs/architecture.md](docs/architecture.md)

## Khi cần hỗ trợ tiếp theo

Dự án dùng **AI-assisted development pattern** (CLAUDE.md + devlog.sqlite). Nếu bạn là dev mới nhận project:
1. Mở project trong Claude Code / Cursor / Cline / aider
2. AI sẽ tự đọc `CLAUDE.md` và `docs/` → hiểu context
3. Hỏi tự nhiên (vd: "thêm modality lipsync"), AI sẽ:
   - Read `docs/architecture.md` để biết pattern
   - Tạo UC mới qua MCP project-agent
   - Add workflow JSON + lib stub
   - Test + log devlog

Cụ thể: xem [HANDOFF.md](HANDOFF.md).

## License notice

Các model dùng trong pipeline có license khác nhau:

| Model | License | Use |
|---|---|---|
| FLUX.1-dev | **Non-commercial** | Personal / research only |
| LTX-Video | **Research only** | Personal / research only |
| F5-TTS | Apache 2.0 | Free commercial |
| Stable Audio Open | CC-BY-NC | Non-commercial |
| Whisper | MIT | Free commercial |
| Ollama models (Qwen/DeepSeek-R1) | Apache 2.0 / MIT | Free commercial |

⚠ Bán hoặc dùng commercial cho video sinh ra từ Flux/LTX/SAO **vi phạm license**. Nếu cần commercial, escalate sang model có license cho phép (Flux.1 Pro paid, hoặc tự train).

## Contributing

Pipeline theo TDD (xem [CLAUDE.md](CLAUDE.md)): mỗi feature có UC + TC + RED test trước GREEN code. Decisions log vào `docs/decision-log.md`.

## License

(Code dự án) MIT — model weights riêng theo license của từng model (xem trên).
