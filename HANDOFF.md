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
Hiện 5 file `workflows/*.json.stub` chỉ là placeholder. Phải làm:

| Stub | Action |
|---|---|
| `flux_keyframe.json.stub` | Mở ComfyUI Manager → Templates → Flux example → Save (API Format) → `workflows/flux_keyframe.json` |
| `ltx_motion.json.stub` | Cài `ComfyUI-LTXVideo` qua Manager → load example workflow → save |
| `f5_tts.json.stub` | Cài `ComfyUI-F5-TTS` → load example → save |
| `stable_audio_music.json.stub` | Cài `ComfyUI-StableAudio` → load example → save |
| `whisper_caption.json.stub` | Cài `ComfyUI-Whisper` → load example → save |

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

## 10. Phase roadmap

| Phase | Status | Effort |
|---|---|---|
| 0. Setup script cross-OS | ✅ DONE | — |
| 1. Workflow JSON thật + smoke test | ⏳ TODO | 2-4 giờ |
| 2. Real video render end-to-end | ⏳ TODO | 1-2 ngày |
| 3. Eval dashboard hoạt động | ⏳ TODO | 1 ngày |
| 4. GUI launcher (Tkinter/PySide) | 🔮 future | 2 ngày |
| 5. Inno Setup installer (Win) | 🔮 future | 8 ngày |
| 6. Web chat router MCP (Adjudicator vote free) | 🔮 future | 3 ngày |
| 7. Auto-discovery + champion/challenger | 🔮 future | 1 tuần |
