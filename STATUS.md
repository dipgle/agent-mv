# Implementation Status — Spec vs Code

[README.md](README.md) mô tả target architecture. File này map từng contract trong
README → file/line nơi nó nên/đang được implement, kèm state hiện tại. **Update mỗi
khi ship một batch trong [TODO.md](TODO.md)**.

Legend:
- ✅ **shipped** — code khớp contract
- ⚠ **partial** — có structure đúng spirit nhưng wired sai object hoặc thiếu nhánh
- ❌ **not built** — contract declare, code không có
- 🔥 **violated** — code hoạt động ngược lại contract (nguy hiểm hơn ❌)

## Summary (snapshot 2026-06-09)

| State | Count | Contracts |
|---|---|---|
| ✅ shipped | 1 | Tier W Web Chat Router |
| ⚠ partial | 2 | License gate (binary, not per-model); Canary (wired to config-diff, not model-swap) |
| ❌ not built | 6 | `quotas.json` registry; quota-aware cascade; provider discovery probes; ratelimit decrement; pricing self-updater; model_list auto-curation |
| 🔥 violated | 1 | README nói bỏ `MAX_COST_PER_VIDEO_USD`; code vẫn enforce $5/$50/$500 cap |

Overall: **~20% match** giữa README contract và code thực tế.

---

## 1. Live quota registry — `infra/quotas.json`

**Contract** ([README.md "Model registry là live"](README.md)): pipeline probe các provider mỗi startup, ghi `infra/quotas.json` với `tokens_remaining` per model, rebuild registry; `cost_gate.py` đọc snapshot này trước mỗi call.

**State**: ❌ **Not built**
- File `infra/quotas.json` không tồn tại trên đĩa.
- `grep -rn "quotas.json\|MODEL_QUOTAS_FILE\|tokens_remaining\|rate_limit_reset\|last_429_at" orchestrator/ infra/` → **0 hits**.

**Path to green**: [TODO.md](TODO.md) Batch 1 + Batch 2.

---

## 2. USD cap removal

**Contract** ([README.md "Chi phí = first-class metric"](README.md)): "KHÔNG có `MAX_COST_PER_VIDEO_USD` — pipeline không bị chặn bởi ngân sách giả định".

**State**: 🔥 **Violated** — code vẫn enforce
- [cost_gate.py:44-46](orchestrator/lib/cost_gate.py#L44-L46) đọc `MAX_PER_VIDEO=5`, `MAX_PER_DAY=50`, `MAX_PER_MONTH=500` từ env (với default cứng).
- [cost_gate.py:256-294](orchestrator/lib/cost_gate.py#L256-L294) `gate()` chạy `headroom = min(MAX_PER_VIDEO - spent_video, MAX_PER_DAY - spent_day, MAX_PER_MONTH - spent_month)` rồi raise `BudgetExceeded` nếu vượt — đây là **active gate** trong pipeline, không phải dead code.
- [cost_rollup.py:43](orchestrator/supervisor/cost_rollup.py#L43) còn `MAX_PER_MONTH` cho alert threshold.
- [.env.example](.env.example) đã drop các biến này (sạch ở env layer), nhưng code không đọc env vẫn fall back về default cứng.

**Path to green**: [TODO.md](TODO.md) Batch 2 (drop constants + headroom math + replace bằng quota lookup).

---

## 3. Quota-aware cascade

**Contract** ([README.md "lib/cost_gate.py quota-aware routing"](README.md)): cascade chọn tier rẻ nhất CÒN QUOTA + alive trong registry; auto-switch khi 429/exhausted.

**State**: ❌ **Not built** — cascade hardcoded
- [cost_gate.py:64-139](orchestrator/lib/cost_gate.py#L64-L139) `_build_cascade(commercial_mode)` trả về `dict[str, str | None]` static, resolve **một lần ở module load** chỉ từ `COMMERCIAL_MODE` env.
- Cascade map (ví dụ `"adjudicator" → "adjudicator-paid" → "reviewer-paid" → ...`) là tay viết theo logical role name, không phải theo tier $/availability.
- [cost_gate.py:269-294](orchestrator/lib/cost_gate.py#L269-L294) cascade trigger điều kiện = `est_cost > headroom`, không phải `quota == 0`.

**Path to green**: [TODO.md](TODO.md) Batch 2.

---

## 4. Provider discovery probes

**Contract** ([README.md "Mỗi lần khởi động pipeline probe"](README.md)): probe Ollama `/api/tags`, OpenRouter `/api/v1/models`, Groq `/openai/v1/models`, Cerebras `/v1/models`, Mistral `/v1/models`, Anthropic `/v1/models`, OpenAI `/v1/models`, Gemini `/v1beta/models`, ComfyUI `/object_info`.

**State**: ❌ **Not built**
- `grep -rn "/api/tags\|/v1/models\|/api/v1/models\|/object_info" orchestrator/ infra/` → 1 hit duy nhất là [scan.py:235](orchestrator/supervisor/scan.py#L235) gọi `civitai.com/api/v1/models` (editorial discovery, không phải capability probe).
- Không có file `orchestrator/lib/discovery.py` hay tương đương.

**Path to green**: [TODO.md](TODO.md) Batch 1.

---

## 5. Ratelimit header decrement

**Contract** ([README.md "decrement tokens_remaining từ provider response headers"](README.md)): mỗi call decrement `tokens_remaining` từ `x-ratelimit-remaining-*` headers; 429 → mark tier exhausted.

**State**: ❌ **Not built**
- `grep -rn "x-ratelimit\|ratelimit-remaining" orchestrator/` → **0 hits**.
- [litellm_client.py](orchestrator/lib/litellm_client.py) parse `usage.prompt_tokens` + `usage.completion_tokens` nhưng **không đọc response headers**.
- [scan.py:240,290,352,404](orchestrator/supervisor/scan.py) chỉ handle 429 như transient retry (`status_code == 429` → backoff), không track quota.
- [cost_rollup.py](orchestrator/supervisor/cost_rollup.py) chỉ SQL aggregate `model_run.cost.total_usd`, không touch ratelimit state.

**Path to green**: [TODO.md](TODO.md) Batch 3.

---

## 6. License gate — per-model metadata

**Contract** ([README.md "License notice"](README.md)): "discovery loop kiểm license metadata mỗi model trước khi đưa vào registry — bất kỳ model nào không pass license gate sẽ bị filter out tự động".

**State**: ⚠ **Partial** — binary mode, không per-model
- [cost_gate.py:51](orchestrator/lib/cost_gate.py#L51) `COMMERCIAL_MODE = env != "0"` là **single env binary**.
- [cost_gate.py:108-137](orchestrator/lib/cost_gate.py#L108-L137) license filtering = **hai cascade dict tay viết** (one for `True`, one for `False`). Không có per-model license metadata field.
- Không có code fetch license từ HuggingFace `/api/models/{id}` hoặc provider metadata.

**Path to green**: [TODO.md](TODO.md) Batch 5.

---

## 7. Cloud pricing self-updater

**Contract** ([README.md "External scan"](README.md) + [cost.py:51](orchestrator/lib/cost.py#L51) comment "Supervisor scan keeps this in sync"): pricing tự cập nhật từ provider pages.

**State**: ❌ **Not built** — comment hứa, code không thực hiện
- [cost.py:53-80](orchestrator/lib/cost.py#L53-L80) `CLOUD_PRICING: dict[str, dict]` là **hardcoded dict** với comment "Updated 2026-06-09 via Anthropic/OpenAI/Google/Groq pricing pages" (manual update).
- `grep -n "CLOUD_PRICING\[" orchestrator/` → 1 hit duy nhất là [cost.py:149](orchestrator/lib/cost.py#L149) đọc dict; **không có code mutate**.
- [scan.py:1-15](orchestrator/supervisor/scan.py#L1-L15) docstring nói "LiteLLM pricing changes (model_prices.json diff vs cached)" nhưng chưa wired vào `CLOUD_PRICING`.

**Path to green**: [TODO.md](TODO.md) Batch 4.

---

## 8. LiteLLM model_list auto-curation

**Contract** (implicit từ "Provider thêm/xoá model bất cứ lúc nào; pipeline KHÔNG hardcode danh sách model"): khi provider deprecate model, model_list của LiteLLM proxy cũng phải drop.

**State**: ❌ **Not built**
- [infra/litellm.yaml](infra/litellm.yaml) là static YAML, pin tay từng `model_name → provider/model_id + api_key env`.
- Không có write/reload hook để supervisor cập nhật YAML từ discovery output.

**Path to green**: Ngoài scope 5 batch hiện tại — discovery xong sẽ chỉ cache vào `quotas.json`, không drive LiteLLM proxy reload. Theo dõi như follow-up sau Batch 5.

---

## 9. Tier W — Web Chat Router

**Contract** ([README.md "Web Chat Router (Tier W)"](README.md)): Adjudicator cascade `Tier W → Codex pool → Opus`; Tier W $0, qua anonymous web UI.

**State**: ✅ **Shipped**
- [mcp/web-chat-router/](mcp/web-chat-router/) đầy đủ: adapters `perplexity.ts`, `lmarena.ts`, `huggingchat.ts`; `redact.ts` (privacy guard), `quota.ts` (per-provider hourly cap), `browser_pool.ts`, `server.ts` (MCP entry).
- [cost_gate.py:151-189](orchestrator/lib/cost_gate.py#L151-L189) Python-side: `is_web_chat()` + `check_web_chat_quota()` + soft quota qua `eval/web_chat_quota.json`.
- [cost_gate.py:244-245](orchestrator/lib/cost_gate.py#L244-L245) `gate()` bypass budget math khi `is_web_chat(intended)` → cost=0.

**Caveat**: README nói Tier W là **default cho phần lớn case**, nhưng cascade dict trong [cost_gate.py:73-95](orchestrator/lib/cost_gate.py#L73-L95) không có Tier W là default cho bất kỳ logical role nào — caller phải gọi explicit với `web_chat/*` model name. Cần wire vào dynamic cascade ở Batch 2.

---

## 10. Supervisor canary — `auto_promote.py`

**Contract** ([README.md "Auto-promote start canary on low-risk proposals; promote/rollback after 7d"](README.md)): canary swap model trong pipeline, evaluate fitness, promote/rollback.

**State**: ⚠ **Partial** — structure đúng spirit, wired sai object
- [auto_promote.py:1-25](orchestrator/supervisor/auto_promote.py) implement canary state machine, stale rollback, snapshot taking, dry-run.
- Object canary đang promote = **config snapshot diffs** (file mutations qua `config_mutator.py`), KHÔNG phải model swap entries trong `quotas.json` registry.
- Sau khi Batch 1-5 land registry: cần follow-up wire canary để chấp nhận `proposal.target = "model_swap"` thay cho chỉ `"config_mutation"`.

**Path to green**: Follow-up sau Batch 5 (ngoài scope queue hiện tại).

---

## Section ngoài contract

Các module dưới đây đã built và stable, không nằm trong gap matrix:

- [supervisor/audit.py](orchestrator/supervisor/audit.py) — daily bottleneck/regression/waste/reliability ✅
- [supervisor/regression_check.py](orchestrator/supervisor/regression_check.py) — baseline drift detection ✅
- [supervisor/propose.py](orchestrator/supervisor/propose.py) — LLM-generated improvement proposals ✅
- [lib/checkpoint.py](orchestrator/lib/checkpoint.py) — pipeline state persistence ✅
- [lib/moderation.py](orchestrator/lib/moderation.py) — NSFW/face/consent gate ✅
- [lib/c2pa.py](orchestrator/lib/c2pa.py) — provenance manifest ✅
- [lib/circuit_breaker.py](orchestrator/lib/circuit_breaker.py) — per-modality failure isolation ✅
- [lib/eval_tier1.py](orchestrator/lib/eval_tier1.py) + [eval_tier2.py](orchestrator/lib/eval_tier2.py) — fitness scoring ✅
