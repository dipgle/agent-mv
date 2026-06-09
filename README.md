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

- **GPU**: NVIDIA 24GB+ VRAM (RTX 3090/4090) HOẶC Apple M-series 64GB+ unified
  (Wan2.1-T2V-14B needs 24-40 GB VRAM depending on resolution; proxy 720p needs ~24 GB)
- **RAM**: 32GB minimum, 64GB recommended
- **Storage**: 200GB SSD free (model weights ~45GB + render output)
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

All default models are commercial-OK (Apache 2.0 / MIT / royalty-free).
See `docs/conventions.md` "License hygiene" for the full policy.

| Layer | Tool | License | Vai trò |
|---|---|---|---|
| Text LLM | Ollama (`qwen3-coder:30b`, `deepseek-r1:14b`, `qwen3:32b`, `qwen2.5-vl:7b`) | Apache 2.0 / MIT | Planner, Reviewer, Researcher |
| Image gen | ComfyUI + **FLUX.1-schnell** | Apache 2.0 | Keyframe per shot (4-step, ~7× faster than dev) |
| Video gen | ComfyUI + **Wan2.1-T2V-14B** | Apache 2.0 | Image-to-video motion |
| Voice TTS | ComfyUI + F5-TTS | Apache 2.0 | Voiceover (zero-shot clone) |
| Music | **Pixabay Music API** + CC0 fallback | Royalty-free / CC0 | BGM (replaces Stable Audio Open) |
| STT | ComfyUI + Whisper large-v3 | MIT | Auto captions |
| Router | LiteLLM proxy | Apache 2.0 | Local → free cloud → paid cascade |
| Orchestrator | CrewAI + LangGraph | MIT | 4-role pipeline |
| Compose | ffmpeg + DaVinci Resolve | LGPL / free | Final assembly |
| Eval | SQLite + vanilla JS dashboard | MIT | Continuous improvement |

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

As of 2026-06-09, all default models in this pipeline are commercial-OK:

| Model | License | Commercial use |
|---|---|---|
| FLUX.1-schnell | Apache 2.0 | Yes |
| Wan2.1-T2V-14B | Apache 2.0 | Yes |
| F5-TTS | Apache 2.0 | Yes |
| Whisper large-v3 | MIT | Yes |
| Pixabay Music API | Pixabay License (royalty-free) | Yes |
| Qwen3 / DeepSeek-R1 | Apache 2.0 / MIT | Yes |

Personal/research-only models (FLUX.1-dev, LTX-Video, Stable Audio Open) are
available via `COMMERCIAL_MODE=0` env var but must NOT be used for client work.
See `docs/conventions.md` "License hygiene" for the full policy and process
for adding new models.

## Web Chat Router (Tier W)

The pipeline's **Adjudicator** role normally calls Claude Opus (paid) or the Codex pool
(free trial, rotated). The **Web Chat Router** gives a third option: query frontier
models through their *anonymous web UI* — zero API cost, zero ToS risk from pool abuse.

### When to use Tier W vs the Codex pool

| Signal | Use Tier W (`web_chat.*`) | Use Codex pool (`adjudicator`) |
|--------|--------------------------|-------------------------------|
| Need a quick 2nd/3rd opinion on a script or hook | Yes | No (overkill, burns quota) |
| Need reproducible, high-quality brand judgment | No | Yes |
| Codex pool exhausted (429) | Automatic cascade | — |
| Budget headroom < $0.50 | Yes | Only if pool has quota |
| Need citations / web search context | Yes (Perplexity) | No |

### Phase 1 providers (no login, no API key)

- **Perplexity** — web search + answer + citations. Good for "what are trending hooks for X?"
- **LMArena** — anonymous side-by-side arena (two random frontier models). Non-deterministic
  model pair; treat both responses as independent opinions.
- **HuggingChat** — HuggingFace chat, anonymous mode. Active model varies by HF's rotation.

### Quick start

```bash
# Install (Mac/Linux)
bash mcp/web-chat-router/install.sh

# Install (Windows)
.\mcp\web-chat-router\install.ps1

# Test manually
npx tsx mcp/web-chat-router/src/server.ts
```

Then add the `web-chat-router` entry to your `.mcp.json` (see `.mcp.json.template`).

Full docs: [mcp/web-chat-router/README.md](mcp/web-chat-router/README.md)

### Privacy guard

The router blocks prompts containing API keys, absolute paths, or client brand
identifiers **before** any browser is launched. This guard cannot be disabled.
Never paste client footage descriptions, brand assets, or proprietary scripts
into Tier W adapters.

## Contributing

Pipeline theo TDD (xem [CLAUDE.md](CLAUDE.md)): mỗi feature có UC + TC + RED test trước GREEN code. Decisions log vào `docs/decision-log.md`.

## License

(Code dự án) MIT — model weights riêng theo license của từng model (xem trên).
