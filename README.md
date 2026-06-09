# Video Production Pipeline — Local-First Multi-Agent

Tự động hoá sản xuất video (TikTok / Reels / YouTube short / explainer) bằng AI local: kịch bản, hình ảnh, video, voiceover, nhạc, caption, dựng phim.

Pipeline 4 vai sản xuất: **Researcher → Planner → Executor (6 modality) → Reviewer** + vai thứ 5 **Supervisor** chạy nền (daily + weekly cron): audit hệ thống, scan ngoài (HF/arxiv/pricing), đề xuất cải tiến, **chi phí sản xuất là metric trục chính**.

Local-first qua Ollama + ComfyUI; escalation cloud (Claude/GPT) tùy chọn qua cascade với cost gate.

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
│   ├── lib/
│   │   ├── devlog.py        ← sqlite event logging
│   │   ├── litellm_client.py ← LLM wrapper (OpenAI-compat → :4000) + cost gate
│   │   ├── comfy_client.py  ← ComfyUI HTTP API client
│   │   ├── cost.py          ← cost estimation (cloud + compute + electricity)
│   │   └── cost_gate.py     ← hard cap per video/day/month + cascade fallback
│   ├── supervisor/          ← Vai 5: R&D agent
│   │   ├── audit.py         ← bottleneck/regression/waste/reliability (daily)
│   │   ├── cost_rollup.py   ← per-video/modality/model rollup (daily)
│   │   ├── scan.py          ← HF/arxiv/pricing/ComfyUI scan (weekly)
│   │   ├── propose.py       ← LLM → improvement proposals (weekly)
│   │   └── auto_promote.py  ← canary + auto-promote low-risk (daily/weekly)
│   └── cron/
│       ├── daily.sh + daily.ps1      ← run audit + rollup + auto-promote
│       └── weekly.sh + weekly.ps1    ← run scan + propose
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
│   ├── schema.sql           ← devlog VIEW extensions (cost/proposals/canary/outcome)
│   ├── golden/              ← fixed benchmark tasks per modality
│   ├── golden_regression/   ← baseline snapshots for drift detection
│   ├── benchmarks/          ← cached pricing + comfy node lists + scan output
│   ├── reports/             ← daily audit + cost + weekly scan + improvement queue
│   ├── canary/              ← active canary state per proposal
│   └── dashboard.html       ← vanilla JS — Cost tab + Proposals tab + Audit tab + per-modality
├── logs/
│   └── devlog.sqlite        ← source of truth (events, UCs, TCs, runs)
└── memory/
    ├── active-context.md
    ├── session-summary.md
    └── discovered-knowledge.md
```

## Supervisor — luôn rà soát, scan ngoài, tối ưu cost

Vai thứ 5 chạy autonomous:

| Job | Cadence | Mục tiêu |
|---|---|---|
| **Audit** (`supervisor/audit.py`) | daily | bottleneck (slowest), regression (vs baseline), waste (duplicate prompts), reliability (success_rate <95%) |
| **Cost rollup** (`supervisor/cost_rollup.py`) | daily | per-video / per-modality / per-model spend; month-to-date burn vs cap |
| **External scan** (`supervisor/scan.py`) | weekly | HF trending + arxiv efficiency papers + LiteLLM pricing diff + ComfyUI new nodes |
| **Propose** (`supervisor/propose.py`) | weekly | LLM-generated improvement proposals from scan findings + audit signals |
| **Auto-promote** (`supervisor/auto_promote.py`) | daily | start canary on low-risk proposals; promote/rollback after 7d |

**Chi phí = first-class metric**:
- `lib/cost.py` tracks cloud + compute + electricity per call
- `lib/cost_gate.py` enforces cap per video (default $5) + cascade fallback to cheaper model
- Dashboard tab "💰 Cost" hiển thị month-to-date burn, top spend videos, cost vs watch-through

Setup cron:
```bash
# Linux/Mac
crontab -e
# Add:
0 2 * * * cd /path/to/agent-mv && bash orchestrator/cron/daily.sh > logs/cron-daily.log 2>&1
0 9 * * 1 cd /path/to/agent-mv && bash orchestrator/cron/weekly.sh > logs/cron-weekly.log 2>&1

# Windows: use Task Scheduler with orchestrator\cron\daily.ps1 / weekly.ps1
```

Cấu hình ngân sách qua env:
```
MAX_COST_PER_VIDEO_USD=5
MAX_COST_PER_DAY_USD=50
MAX_COST_PER_MONTH_USD=500
PIPELINE_HARDWARE=M3_Max_owned   # or RTX_4090_owned, RTX_4090_runpod, ...
ELECTRICITY_USD_KWH=0.12
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
