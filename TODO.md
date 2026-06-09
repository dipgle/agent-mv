# Implementation TODO — Path to Match README Contract

5 batch theo **dependency order** để đóng gap matrix trong [STATUS.md](STATUS.md).
Mỗi batch land một cột; ship xong update STATUS.md tương ứng. Không reorder — batch sau
consume artifact của batch trước.

---

## Batch 1 — Quota registry schema + discovery probes

**Goal**: `infra/quotas.json` tồn tại với danh sách model thực tế từ probe các provider.

**Closes**: STATUS §1 (live registry), §4 (discovery probes).

- [ ] Define schema `infra/quotas.schema.json`:
  - `model_id`, `provider`, `tier` (local/free/paid), `tokens_remaining` (nullable cho local/paid),
    `tokens_quota_window` (daily/hourly/null), `rate_limit_reset_at`, `last_429_at`,
    `last_seen_at`, `license`, `deactivated` (bool), `capabilities` (text/vision/audio/image/video)
- [ ] Tạo `orchestrator/lib/discovery.py` với probe functions:
  - `probe_ollama()` → `GET http://localhost:11434/api/tags`
  - `probe_comfyui()` → `GET http://localhost:8188/object_info`
  - `probe_openrouter()` → `GET https://openrouter.ai/api/v1/models`
  - `probe_groq()` → `GET https://api.groq.com/openai/v1/models`
  - `probe_cerebras()` → `GET https://api.cerebras.ai/v1/models`
  - `probe_mistral()` → `GET https://api.mistral.ai/v1/models`
  - `probe_anthropic()` → `GET https://api.anthropic.com/v1/models`
  - `probe_openai()` → `GET https://api.openai.com/v1/models`
  - `probe_gemini()` → `GET https://generativelanguage.googleapis.com/v1beta/models`
- [ ] `rebuild_registry()` — merge probe outputs, atomic write `infra/quotas.json.tmp → rename`.
- [ ] Missing API key → tier skipped (warning event), không crash.
- [ ] CLI: `python -m orchestrator.lib.discovery refresh` + log event `kind=registry_refresh`.
- [ ] Tests: each probe returns ≥ 1 model với valid creds; missing creds → tier skip; malformed JSON → tier skip + error log.

---

## Batch 2 — Quota-aware cascade trong cost_gate.py

**Goal**: Drop USD cap; cascade chọn tier rẻ nhất còn quota từ registry.

**Closes**: STATUS §2 (USD cap violation), §3 (cascade hardcoded), part of §9 caveat (wire Tier W default).

- [ ] [cost_gate.py](orchestrator/lib/cost_gate.py): xoá `MAX_PER_VIDEO`, `MAX_PER_DAY`, `MAX_PER_MONTH` constants + `_spent_per_video`, `_spent_today`, `_spent_this_month` helpers.
- [ ] Xoá `headroom` math trong `gate()`; thay bằng `pick_cheapest_with_quota(role, capability_required) → model_id`.
- [ ] Static `_build_cascade()` dict → dynamic `tier_priority_for(role, capability)` derived from registry (sort by `tier_cost_rank` + filter `tokens_remaining > 0` + filter `not deactivated`).
- [ ] Wire Tier W là default cho `adjudicator` role (theo README) — registry entry với `tier=web_chat`, `cost=0` được pick trước Codex pool.
- [ ] [cost_rollup.py:43](orchestrator/supervisor/cost_rollup.py#L43): drop `MAX_PER_MONTH` import + alert threshold logic (sẽ refactor lại ở Batch 3 dựa trên quota events).
- [ ] Update `remaining_budget()` return shape — không còn `*_remaining_usd`, thay bằng `tokens_remaining_per_tier`.
- [ ] Tests:
  - cascade skip tier có `deactivated=true`
  - cascade pick tier rẻ nhất với `tokens_remaining > 0`
  - tất cả tier exhausted → raise `NoQuotaAvailable` (rename từ `BudgetExceeded`)
  - Tier W là default cho adjudicator khi registry có web_chat entry

---

## Batch 3 — Ratelimit header decrement

**Goal**: Mỗi model call cập nhật `quotas.json` từ response headers.

**Closes**: STATUS §5.

- [ ] [litellm_client.py](orchestrator/lib/litellm_client.py): parse từ `resp.response.headers`:
  - `x-ratelimit-remaining-tokens`, `x-ratelimit-remaining-requests`
  - `x-ratelimit-reset-tokens`, `x-ratelimit-reset-requests`
  - `retry-after` (cho 429)
- [ ] Hàm `decrement_quota(model_id, headers, status_code)`:
  - 200 → `tokens_remaining = headers.x-ratelimit-remaining-tokens`
  - 429 → `last_429_at = now`, `tokens_remaining = 0` cho đến `rate_limit_reset_at`
  - Atomic write `quotas.json.tmp → rename`
- [ ] Tương tự cho `comfy_client.py` (probe ComfyUI free slot, không có ratelimit headers thật).
- [ ] Webhook alerting ở [.env.example](.env.example) `COST_ALERT_LEVEL` được hooked qua quota events (free tier exhausted, paid tier used, refill detected) — refactor `cost_rollup.py` để trigger từ quota state diff, không phải MTD %.
- [ ] Tests:
  - Mock response với `x-ratelimit-remaining-tokens: 1000` → quotas.json cập nhật đúng
  - Mock 429 → tier marked exhausted, next cascade skip tier đó
  - Quota refill (reset_at trôi qua) → tier active lại

---

## Batch 4 — Cloud pricing self-updater

**Goal**: `CLOUD_PRICING` không còn hardcoded; pricing pull từ provider hoặc cached JSON.

**Closes**: STATUS §7.

- [ ] Move [cost.py:53-80](orchestrator/lib/cost.py#L53-L80) `CLOUD_PRICING` dict → `eval/benchmarks/cloud_pricing.json` (with `last_updated`, `source_url` per model).
- [ ] [cost.py:149](orchestrator/lib/cost.py#L149) đọc JSON thay vì hardcoded dict; cache trong process memory với TTL 1h.
- [ ] [supervisor/scan.py](orchestrator/supervisor/scan.py): thêm step `scan_provider_pricing()`:
  - Anthropic: scrape `https://www.anthropic.com/pricing` hoặc dùng [LiteLLM model_prices.json](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json)
  - OpenAI/Google/Groq/Cerebras: tương tự
  - Diff vs cached, merge into JSON, log event `kind=pricing_update` với diff.
- [ ] Stale pricing (> 30d không update) → warning trong `supervisor/audit.py` daily report.
- [ ] [cost.py](orchestrator/lib/cost.py) fallback to last-known on missing/malformed JSON; never crash.
- [ ] Tests: pricing mutator round-trip; cost estimation cho model không có trong JSON → log + default rate; stale flag triggers audit warning.

---

## Batch 5 — Per-model license metadata + gate

**Goal**: License gate decide per-model từ metadata, không phải `COMMERCIAL_MODE` binary flag.

**Closes**: STATUS §6.

- [ ] Discovery probe (Batch 1) enrich `quotas.json` với `license` field:
  - HuggingFace models: `GET https://huggingface.co/api/models/{id}` → `cardData.license`
  - Provider models (OpenAI/Anthropic/Google): hardcoded mapping (proprietary, paid tier)
  - Ollama local: parse Modelfile / model card
  - Pixabay/ElevenLabs/Runway: hardcoded license tag
- [ ] [cost_gate.py:51,108-137](orchestrator/lib/cost_gate.py): drop `COMMERCIAL_MODE` binary + 2 cascade variants.
- [ ] Replace bằng `license_allowed_for(license, mode)`:
  - `mode=commercial` (default): chấp nhận `Apache-2.0`, `MIT`, `BSD-3`, `CC0`, `CC-BY`, paid commercial
  - `mode=research`: enlarge với `CC-BY-NC`, `BFL-NC`, research-only
- [ ] `pick_cheapest_with_quota()` (từ Batch 2) thêm filter `license_allowed_for(model.license, env.LICENSE_MODE)`.
- [ ] [infra/models.md](infra/models.md): regenerate từ `quotas.json` filtered to commercial-OK tier (human-readable mirror).
- [ ] Tests:
  - HF discovery thấy model `CC-BY-NC` → default mode filter out
  - `LICENSE_MODE=research` → model `CC-BY-NC` lọt qua gate
  - License metadata missing → conservative reject (assume non-commercial)

---

## Out of scope (follow-up sau Batch 5)

- **Canary refactor for model swap** ([STATUS §10](STATUS.md)) — `auto_promote.py` đang promote config-diffs. Cần extend `proposal.target` schema để accept `"model_swap"` (registry entry diff) + canary traffic split per-model.
- **LiteLLM model_list auto-curation** ([STATUS §8](STATUS.md)) — discovery hiện chỉ cache vào `quotas.json`, không drive LiteLLM proxy YAML reload. Cần `infra/litellm.yaml.tmpl` + supervisor regenerate + proxy hot-reload signal.
- **Tier W cascade dynamic placement** ([STATUS §9 caveat](STATUS.md)) — Batch 2 chỉ wire Tier W default cho `adjudicator`. Mở rộng cho Researcher/Reviewer cần phân tích cost/quality trade-off riêng.

---

## Sequencing rationale

```
Batch 1 (registry schema + probes) ──┐
                                      ├──► Batch 2 (cascade reads registry)
                                      │         │
                                      │         ├──► Batch 3 (writes back from headers)
                                      │         │
                                      │         └──► Batch 5 (filters by license field)
                                      │
                                      └──► Batch 4 (pricing JSON external từ registry)
```

- Batch 2 không thể start nếu registry schema chưa chốt (Batch 1).
- Batch 3 (ghi back) cần `quotas.json` đã tồn tại với schema ổn định (Batch 1) và cascade hook để test end-to-end (Batch 2).
- Batch 4 độc lập về schema nhưng cần `eval/benchmarks/` convention từ Batch 1.
- Batch 5 cần `license` field đã có trong registry schema (Batch 1) + cascade filter hook (Batch 2).

Tổng ước: 5 batch ≈ 2-3 tuần single dev nếu test coverage giữ mức hiện tại.
