# Handoff — Context cho dev / AI mới nhận project

Mở file này TRƯỚC khi đụng code. Cấu trúc theo thứ tự đọc.

## 1. TL;DR

Multi-agent local-first video pipeline (4 roles, 6 modality, ComfyUI + Ollama + LiteLLM). Skeleton **chạy được** đến mức:
- ✅ Setup script auto-install all deps trên Win/Mac/Linux
- ✅ LiteLLM router với cascade B → A− → A → S
- ✅ Devlog SQLite tracking mọi model call + asset
- ✅ Pipeline.py implement 4 roles + 6 sub-executor + ffmpeg compose
- ✅ Eval VIEWs + dashboard skeleton
- ⚠ ComfyUI workflows = STUB (file `.json.stub`) — phải export workflow thật từ ComfyUI UI trước khi render được
- ⚠ Researcher LLM scraping = stub (manual fill `reference.json` để bypass)
- ⚠ Adjudicator role = log decision only, chưa auto-call Opus

## 2. Đọc theo thứ tự

| # | File | Mục đích |
|---|---|---|
| 1 | [README.md](README.md) | Overview + quick start + cấu trúc |
| 2 | [PLAYBOOK.md](PLAYBOOK.md) | Design quick read (1 trang) |
| 3 | [docs/architecture.md](docs/architecture.md) | 4 roles, tier system, workflow chi tiết |
| 4 | [docs/conventions.md](docs/conventions.md) | Quy tắc vàng — model selection, hybrid, eval, privacy |
| 5 | [CLAUDE.md](CLAUDE.md) | AI assistant rules (init-project template) |
| 6 | [infra/models.md](infra/models.md) | Model inventory + role mapping |
| 7 | [orchestrator/pipeline.py](orchestrator/pipeline.py) | Entry point — đọc code đây để hiểu flow |
| 8 | [orchestrator/lib/](orchestrator/lib/) | Helper libs (devlog, LiteLLM, ComfyUI) |
| 9 | [workflows/README.md](workflows/README.md) | Cách lấy/build ComfyUI workflows |
| 10 | [eval/schema.sql](eval/schema.sql) | SQL VIEWs cho dashboard |

## 3. Việc cần làm để chạy được end-to-end

### Phase 0 — Setup (1-3 giờ)
1. Chạy `infra/setup.sh` (Mac/Linux) hoặc `infra/setup.ps1` (Windows)
2. Boot 3 services: Ollama / ComfyUI / LiteLLM
3. Copy `.env.example` → `.env`, điền keys nếu cần cloud

### Phase 1 — Workflows thật (2-4 giờ)
Hiện có `workflows/*.json.stub` chỉ là placeholder. Phải làm:

| Stub | Action |
|---|---|
| `flux_schnell_keyframe.json.stub` | Cài `ComfyUI-FluxDev` qua Manager → load FLUX.1-schnell checkpoint → KSampler: steps=4, cfg=0.0, scheduler=simple → Save (API Format) → `workflows/flux_schnell_keyframe.json` |
| `wan21_motion.json.stub` | Cài `ComfyUI-WanVideo` qua Manager → load Wan2.1-T2V-14B workflow → save as `workflows/wan21_motion.json` |
| `f5_tts.json.stub` | Cài `ComfyUI-F5-TTS` → load example → save |
| `whisper_caption.json.stub` | Cài `ComfyUI-Whisper` → load example → save |
| `stock_music_search.json.stub` | Không phải ComfyUI workflow — đọc instructions bên trong, set `PIXABAY_API_KEY` trong `.env` |

Note: `stable_audio_music.json.stub` đã bị xóa (CC-BY-NC license). Music giờ qua Pixabay API — xem `orchestrator/lib/stock_music.py`.

Sau đó update mapping node ID trong `orchestrator/pipeline.py` (search "NODE IDs depend on"):
```python
patches = {
    "6": {"text": shot["image_prompt"]},   # CLIPTextEncode node id from your workflow
    "5": {"width": w, "height": h},        # EmptyLatentImage node id
    ...
}
```

### Phase 2 — Smoke test (30 phút)
```bash
python orchestrator/pipeline.py \
    --intent "Test 5s clip blue sky" \
    --feature-id SMOKE-001 \
    --aspect 16:9 \
    --duration 5
```
Nếu chạy đến `final.mp4` không lỗi = phase 0+1 thành công. Critique không quan trọng ở smoke.

### Phase 3 — Real video (1-2 ngày tinh chỉnh)
- Tinh chỉnh prompt template trong `pipeline.py` PLANNER_SYSTEM
- Tạo brand.json riêng (clone `brand-example.json`)
- Add voice reference WAV cho F5-TTS clone
- Iterate khi Reviewer reject

## 4. Stub-marked components (cần fill khi mở rộng)

| Component | Trạng thái | File |
|---|---|---|
| Researcher LLM scrape | manual fill JSON | `orchestrator/pipeline.py:researcher()` |
| Adjudicator auto-call Opus | log decision only | `orchestrator/pipeline.py:main()` cuối |
| Web chat router MCP | chưa build | (xem `docs/architecture.md` Section 10) |
| Auto-discovery cron | chưa build | `eval/discover.sh` chưa tạo |
| Dashboard data fetch | UI có, API thiếu | `eval/dashboard.html` (TODO fetch HTTP endpoint) |
| Adjudicator GUI launcher | chưa build | (xem Phase 4-5 roadmap) |

## 5. AI assistant hand-off pattern

Project có `CLAUDE.md` với rules cứng cho AI assistant (TDD, devlog everything, use-case first, question discipline). Khi bạn mở project trong Claude Code / Cursor / Cline / aider:

1. AI tự read `CLAUDE.md` + `docs/kickoff.md`
2. Nếu `kickoff.md` trống → AI sẽ chạy discovery dialog (vision/actor/success/non-goals/constraints/UCs)
3. Sau discovery, AI vào TDD cycle: RED (write failing test) → GREEN (minimum code) → REFACTOR
4. Mọi step log vào `logs/devlog.sqlite` qua MCP project-agent

→ **Bạn không phải dạy AI từ đầu.** Cứ chat tự nhiên ("thêm lipsync modality", "fix workflow F5 không gen được tiếng Việt"), AI sẽ tự đọc context + execute.

## 6. MCP project-agent

`.mcp.json` đăng ký 1 MCP server tên `project-agent` (Rust binary). Tools available:
- `get_context_brief` — load active UCs + warnings (gọi đầu session)
- `next_task` — gợi ý task tiếp theo
- `scan_health` — phát hiện UC stale, test fail recurring
- `log_event` — append event devlog
- `create_use_case`, `create_test_case`, `record_test_run`
- `list_use_cases`, `list_test_cases`, `recent_events`

AI assistant tự dùng các tools này. Bạn không cần gọi tay.

## 7. Constraint quan trọng (vi phạm = pipeline vỡ)

| Constraint | Lý do |
|---|---|
| Autocomplete model PHẢI là `:base` variant | Instruct model không có FIM tokens → autocomplete sai |
| Reviewer family KHÁC Executor family | Cùng family hay đồng tình lỗi nhau |
| `workflows/*.json` không được commit là `.stub` | Pipeline detect stub → throw error rõ ràng (KHÔNG chạy bừa) |
| Devlog events kind=`source` BẮT BUỘC khi consult external URL | Question Discipline rule (xem CLAUDE.md) |
| KHÔNG paste client brand asset vào free web chat | Privacy + train data leak |
| Code comments in English | Convention (memory: code-comments-english) |

## 8. Khi stuck

1. `cat memory/active-context.md` — xem session trước làm gì
2. `sqlite3 logs/devlog.sqlite "SELECT ts, kind, content FROM events ORDER BY id DESC LIMIT 20"` — gần đây làm gì
3. `cat docs/decision-log.md` — quyết định đã chốt
4. `cat docs/testing-knowledge.md` — edge case đã biết
5. Hỏi AI assistant (Claude / GPT / Cursor / Cline) — đã có CLAUDE.md hướng dẫn

## 9. Liên hệ tác giả gốc

(Tùy bạn điền — Slack/email/GitHub issue tracker)

## 10. Supervisor (vai 5) — đã ship Phase 1

Đã có module supervisor autonomous chạy daily + weekly cron:
- `supervisor/audit.py` — bottleneck/regression/waste/reliability
- `supervisor/cost_rollup.py` — per-video/modality/model spend rollup
- `supervisor/scan.py` — HF/arxiv/pricing/ComfyUI scan
- `supervisor/propose.py` — LLM-generated improvement proposals
- `supervisor/auto_promote.py` — canary + auto-promote low-risk

Cost tracking là first-class: mỗi model_run event có `cost.cloud_usd + compute_usd + electricity_usd`. Cost gate ở `lib/cost_gate.py` enforces cap per video/day/month với cascade fallback.

Setup cron để supervisor chạy auto:
```bash
crontab -e
0 2 * * * cd <project> && bash orchestrator/cron/daily.sh > logs/cron-daily.log 2>&1
0 9 * * 1 cd <project> && bash orchestrator/cron/weekly.sh > logs/cron-weekly.log 2>&1
```

Outputs vào `eval/reports/`:
- `audit_YYYY-MM-DD.md` — daily audit
- `cost_YYYY-MM-DD.md` — daily cost rollup
- `scan_YYYY-MM-DD.md` — weekly external scan
- `improvement_queue.md` — pending proposals sorted by priority

Stub phần còn lại (real implementation cần dev fill):
- `auto_promote.py` chỉ log decision; chưa thực sự apply config change (cần modify `infra/litellm.yaml` hoặc `workflows/*.json`)
- `propose.py` LLM call route qua `planner` (local); cho proposal phức tạp nên route sang `planner-script-hard` (Claude Opus)
- `audit.py` regression check cần baseline snapshot — chạy `regression_check.py snapshot` 1 lần khởi điểm

## 11. Windows compatibility checklist

Codebase ship cross-OS. Verify trên Windows test box:

| Item | How | Expected |
|---|---|---|
| Line endings | `git clone` → `.gitattributes` áp dụng | `*.sh` LF, `*.ps1` CRLF, `*.py` LF |
| Env check | `.\run.ps1 check --verbose` | Tất cả `[ OK ]` trừ services có thể `[WARN]` chưa start |
| Setup | `.\run.ps1 setup` | Installs Python/Git/ffmpeg/Ollama/ComfyUI/weights |
| Smoke render | `.\run.ps1 pipeline --intent "test" --feature-id WIN-TEST --duration 5` | `out\WIN-TEST\final.mp4` tồn tại |
| Audit cron | `.\run.ps1 cron-daily` | `eval\reports\audit_*.md` + `cost_*.md` |
| External scan | `.\run.ps1 cron-weekly` | `eval\benchmarks\external_sources_*.json` |
| Dashboard | `.\run.ps1 dashboard` | Browser mở `eval\dashboard.html` |
| Task Scheduler | INSTALL-WIN.md step 9 | `Get-ScheduledTask AgentMV-*` shows 2 tasks |
| Unicode console | Vietnamese chars trong report | Hiển thị đúng không `???` |
| Compose shell fallback | Pipeline tự gọi `powershell` nếu thiếu `pwsh` | No `pwsh: command not found` error |

Known Windows-specific quirks (đã handle trong code):
- `lib/litellm_client.py` env var name uppercase auto-handled by Python's `os.environ`
- `lib/cost_gate.py` SQLite path resolved via `Path("logs/devlog.sqlite")` (works both `/` and `\`)
- `orchestrator/pipeline.py:_resolve_compose_cmd()` thử `pwsh` trước, fallback `powershell.exe`
- `orchestrator/_console.py:ensure_utf8()` được gọi đầu mọi CLI entry — fixes mojibake
- `run.ps1` set `chcp 65001` + `$env:PYTHONIOENCODING="utf-8"` global

Còn TODO test trên Windows VM clean (chưa có physical Windows box):
- Verify winget install order (Python phải trước venv create)
- Verify `huggingface-cli` ở venv vs system PATH
- Verify Task Scheduler chạy cron trong context đúng (CWD = project root)
- Verify Defender không quarantine `python.exe` trong venv

## 12. Phase roadmap

| Phase | Status | Effort |
|---|---|---|
| 0. Setup script cross-OS | ✅ DONE | — |
| 1. Cost tracking + Supervisor cron foundation | ✅ DONE | — |
| 2. Workflow JSON thật + smoke test | ⏳ TODO | 2-4 giờ |
| 3. Real video render end-to-end | ⏳ TODO | 1-2 ngày |
| 4. Outcome API client (YouTube/TikTok/Meta) → calibrate panel | ⏳ TODO | 3 ngày |
| 5. Tier 2 reviewer panel ensemble (4 model) | ⏳ TODO | 2 ngày |
| 6. Auto_promote.py: actually mutate config + roll back | ⏳ TODO | 2 ngày |
| 7. Eval dashboard data endpoint (HTTP serving SQL VIEWs) | ⏳ TODO | 1 ngày |
| 8. GUI launcher (Tkinter/PySide) | 🔮 future | 2 ngày |
| 9. Inno Setup installer (Win) | 🔮 future | 8 ngày |
| 10. Web chat router MCP (Adjudicator vote free) | 🔮 future | 3 ngày |
