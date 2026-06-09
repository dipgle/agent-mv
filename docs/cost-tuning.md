# Cost Tuning Guide

This guide explains how to interpret cost reports, set budgets, and make tier decisions for managing LLM + compute costs.

## Cost Structure

Every model call logs three cost components:

| Component | What it is | Tracked in |
|---|---|---|
| **cloud_usd** | API cost (Claude, GPT, etc.) | devlog `cost.cloud_usd` |
| **compute_usd** | Local VRAM/compute cost (GPU rental equivalent) | devlog `cost.compute_usd` |
| **electricity_usd** | Electricity to run GPU for duration | devlog `cost.electricity_usd` |

**Total cost per call** = sum of all three.

Example:
- Planner calls Claude Sonnet: `cloud=$0.50 + compute=$0.02 + electricity=$0.01 = $0.53`
- Planner calls local qwen3:32b: `cloud=$0 + compute=$0.15 + electricity=$0.05 = $0.20`

## Reading Daily Cost Reports

After running `orchestrator/cron/daily.sh`, check `eval/reports/cost_YYYY-MM-DD.md`.

Example report structure:

```markdown
# Daily Cost Rollup — 2026-06-09

## Summary
- Total today: $42.50
- Budget: $50.00/day (85% used)
- Month-to-date: $850 / $1000 (85% used)

## By Video
| Video ID | Cost | Modality | Top Model | Notes |
|---|---|---|---|---|
| VID-001 | $12.50 | voice + keyframe | F5-TTS local, Flux | 2 iterations |
| VID-002 | $18.00 | review + script | Claude Sonnet, qwen3 | Adjudicator escalation |
| VID-003 | $11.00 | composite | ffmpeg, local | 5 retries, OOM once |

## By Modality
| Modality | Cost | Count | Avg/call |
|---|---|---|---|
| Keyframe (Flux) | $8.50 | 12 calls | $0.71/img |
| Motion (LTX-Video) | $0 | 8 calls | $0/call (local) |
| Voice (F5-TTS) | $0 | 3 calls | $0/call (local) |
| Planner (text) | $15.00 | 4 calls | $3.75/call |
| Reviewer (text+vision) | $17.50 | 5 calls | $3.50/call |

## By Tier
| Tier | Cost | Role(s) | Budget impact |
|---|---|---|---|
| S (Frontier — Opus) | $25.00 | Adjudicator | —— priority escalation |
| A (Strong — Sonnet) | $17.50 | Reviewer, Planner | —— cascade fallback |
| A− (Free API — Groq) | $0 | Researcher bulk | —— high volume |
| B (Local — Qwen3, DeepSeek) | $0 | Planner, Reviewer | —— preferred baseline |

## Warnings
- Codex pool near exhaustion: 120 calls left (estimated 2–3 more videos)
```

### Interpreting Columns

- **% Budget Used**: How much of your daily cap is consumed. Yellow at 70%, red at 90%.
- **Top Model**: Which model consumed most cost for that video
- **Notes**: Why cost was high (OOM → retry, escalation, multiple iterations)

### What Good Daily Spend Looks Like

For a 30-second video:

| Scenario | Expected cost |
|---|---|
| Local-only (no cloud calls) | $0.30 (electricity only) |
| 2× Planner iterations + 1× Reviewer | $8–12 (local script, cloud review) |
| Full pipeline with 1 Adjudicator escalation | $20–25 |
| Multiple iterations + cloud fallback | $40+ |

If daily spend > $50, investigate:
1. Are you iterating too many times? (Reviewer rejecting)
2. Did cost gate fail to cascade? (Check devlog)
3. Are you calling expensive models unnecessarily? (Check `infra/litellm.yaml`)

## Setting Budget Caps

Budget caps are enforced in this order:

1. **Per-video cap** (tightest): `MAX_COST_PER_VIDEO_USD` (default $5)
2. **Per-day cap**: `MAX_COST_PER_DAY_USD` (default $50)
3. **Per-month cap**: (informational, not enforced in code yet)

When exceeded at any level, **cascade fallback** kicks in.

### Setting Caps via Environment

```bash
# Add to .env file or export before running
export MAX_COST_PER_VIDEO_USD=10   # Can overspend 1 video to $10 before fallback
export MAX_COST_PER_DAY_USD=100    # Can spend $100/day
export MAX_COST_PER_MONTH_USD=2000 # Informational only (not enforced)

# Alternative: pass to CLI
python orchestrator/pipeline.py \
    --intent "..." \
    --feature-id "..." \
    --max-cost-per-video 15 \
    --max-cost-per-day 150
```

### Cascade Behavior Explained

When a cost gate trips:

```
[Planner wants to call Claude Opus, estimated cost $5]
  ↓
[CostGate checks: per-video budget is $3 left (spent $2 already)]
  ↓
[Escalation blocked: $5 > $3]
  ↓
[Cascade fallback: try cheaper model]
  ↓
[qwen3:32b (local) available? YES → use it ($0.15 cost)]
  ↓
[Log decision: "Planner fallback → qwen3:32b due to cost gate"]
  ↓
[Continue render]
```

Fallback order (per role):
- **Planner**: Claude Opus → Claude Sonnet → Groq → qwen3:32b → deepseek-r1:14b
- **Reviewer**: Claude Opus → Claude Sonnet → qwen3:8b → inference failure (retry via ffmpeg auto-check)
- **Researcher**: Claude Opus → Groq → qwen2.5-vl:7b
- **Executor**: No fallback (local only) — reduces resolution instead

## Tier Table with Model Examples

### S — Frontier Cloud (Expensive, Best Quality)

**Models:** Claude Opus, GPT-5

| Model | Cost | Use case | When |
|---|---|---|---|
| Claude Opus | $15/MTok input, $45/MTok output | Adjudicator brand vote, Architect campaign | After 3× Reviewer rejects |
| GPT-5 (via OpenAI) | ~$10/MTok (estimated) | Codex pool (free trial), Adjudicator | Experimental, free pool while available |

**Spend guardrails:**
- Adjudicator: max 1–2 calls/video ($10–20 per escalation)
- Architect: only on approved campaigns ($50+ per run)

### A — Strong Cloud (Balanced, Good Quality)

**Models:** Claude Sonnet, GPT-4o, Gemini Pro

| Model | Cost | Use case | When |
|---|---|---|---|
| Claude Sonnet | $3/MTok input, $15/MTok output | Reviewer escalation (final check), Planner taste | Cascade fallback, or for brand-sensitive videos |
| GPT-4o | $5/MTok input, $15/MTok output | Alternative to Sonnet | If Sonnet quota exhausted |
| Gemini Pro | $0 (free tier limit) | Bulk researcher transcript | Low quota, no fallback |

**Spend guardrails:**
- Reviewer: max 2 calls per video ($5–8 per retry)
- Planner: max 1 call per video ($4–6)

### A− — Free API (Good, Low Cost)

**Models:** Groq (LLaMA 3.1 70B), Cerebras (LLaMA 3.1 70B), Codestral (Mistral)

| Model | Cost | Use case | When |
|---|---|---|---|
| Groq LLaMA 3.1 70B | Free, 30K req/day | Planner fallback, Researcher bulk | High volume, tight budget |
| Cerebras LLaMA 3.1 70B | Free, 20K req/day | Planner alt, Researcher backup | Secondary fallback |
| Codestral | Free, 100K req/month | Code-heavy planner scripts | Tech explainer videos |

**Spend guardrails:**
- Combine quota (70K req/day) — use round-robin across all three
- Each video uses ~2–3 requests
- Estimated: 20–30 videos/day possible

### B — Local Frontier (Free, Very Good)

**Models:** Qwen3 (32B/8B), DeepSeek-R1 (14B), Qwen2.5-VL (7B)

| Model | Size | VRAM | Latency | Use case |
|---|---|---|---|---|
| qwen3:32b-thinking | 32B | 24 GB | ~1.5 min | Planner (preferred baseline) |
| deepseek-r1:14b | 14B | 9 GB | ~45s | Reviewer reasoning |
| qwen2.5-vl:7b | 7B | 8 GB | ~8s | Researcher vision parsing |
| qwen3:8b | 8B | 6 GB | ~5s | Reviewer fallback, Planner lite |

**Spend guardrails:**
- No cloud cost: cost is electricity only (~$0.01–0.05 per call)
- Preferred for all non-escalation calls
- Load all 4 models in Ollama for fast switching

### C — Local Fast (Free, Good)

**Models:** Qwen3:8B (repeated), Phi-3, Mistral 7B

| Model | Latency | Notes |
|---|---|---|
| qwen3:8b | 5s | Can quantize to Q4 for 3s latency |
| Phi-3 | 8s | Lightweight, good for edge |
| Mistral 7B | 6s | Code-friendly |

**Use:** Only if tier B models OOM or too slow.

### W — Web Chat Farm (Deprecated, Low ROI)

Original design: free web chat UI (Claude/GPT/Gemini) for Adjudicator brand voting.

**Status:** Planned but not implemented (Phase 10+). Too slow + UI friction. Prefer Opus CLI instead.

## Spend Optimization Strategies

### Strategy 1: Prefer Local for Planner (Save 95%)

```
Before:
- Planner: Claude Opus every render → $15 × 100 videos = $1500/month
- Cost per video: $15

After:
- Planner: qwen3:32b local, escalate Opus only for brand campaigns → $0 × 100 videos = $0
- Adjudicator: Opus $15 × 5 campaigns = $75
- Cost per video: $0.75

Savings: 95%
```

**How:** In `infra/litellm.yaml`, set Planner default to `qwen3:32b-thinking`, keep Opus as fallback.

### Strategy 2: Batch Reviewer Calls (Save 40%)

```
Before:
- Reviewer: 1 review per video, calls Claude Sonnet → $6 per video × 100 = $600

After:
- Batch 10 videos together, 1 Reviewer call per batch → $6 × 10 = $60
- (Works only if you're comfortable with latent feedback)

Savings: 90%
```

**Tradeoff:** Slower iteration loop (review feedback lags 10-video batch).

### Strategy 3: Use Free APIs Aggressively (Save 50%)

```
Before:
- All Researcher calls: Claude Opus → $1/video × 100 = $100

After:
- Researcher: split 50% Groq, 50% Cerebras (free) → $0
- Fallback: Opus if both free pools exhausted

Savings: 100%
```

**How:** In cost_gate.py, check Groq/Cerebras quota first, only escalate if both hit.

## Codex Pool Discipline

**Warning:** Codex pool (free GPT-5 trial accounts) has strict governance.

| Rule | Why |
|---|---|
| ONLY Adjudicator + Architect | Prevent budget burndown on cheap Executor calls |
| 3 accounts rotated | Extend pool life (3 × $5–18 credit ≈ 500 calls) |
| 429 (quota exceeded) → fallback to paid Opus | Auto-escalate when pool hits limit |
| Monitoring via devlog | Cost gate logs each pool swap |

Example devlog entries:
```sql
SELECT ts, actor, content FROM events 
WHERE actor='cost_gate' AND content LIKE '%pool%'
ORDER BY ts DESC LIMIT 10;

2026-06-09 14:32:10 cost_gate | Codex pool key_3 exhausted, swapped to key_1
2026-06-09 12:01:45 cost_gate | Codex pool all exhausted, escalating to Claude Opus (cost: $45)
```

## Quarterly Budget Planning

**Example: $500/month budget for 100 videos**

| Category | Budget | Notes |
|---|---|---|
| Planner (qwen3:32b, no escalation) | $0 | Local only |
| Reviewer (Sonnet 3× fallback) | $50 | ~2 escalations per 10 videos |
| Researcher (Groq free pool, Opus backup) | $25 | Mostly free, emergency Opus |
| Adjudicator (3 campaign votes @ Opus) | $150 | ($15 × 10 calls) |
| Codex pool fallback (if exhausted) | $150 | Emergency budget |
| Electricity (local GPU) | $125 | Owned M3 Max 64GB @ $0.12/kWh |
| Total | $500 | ✓ On budget |

## Monthly Monitoring

After running `orchestrator/cron/daily.sh` every day:

1. **First of month**: Summarize previous month
   ```bash
   sqlite3 logs/devlog.sqlite \
     "SELECT strftime('%Y-%m', ts) AS month, \
             COUNT(*) AS calls, \
             ROUND(SUM(CAST(json_extract(metadata, '$.cost.cloud_usd') AS FLOAT)), 2) AS total_cloud \
      FROM events \
      WHERE kind='model_run' \
      GROUP BY month \
      ORDER BY month DESC"
   ```

2. **Mid-month**: Check burn rate vs budget
   ```bash
   # Is MTD spend on track?
   # If > 50% of budget by day 15, consider tightening caps
   ```

3. **End of month**: Archive reports
   ```bash
   cp eval/reports/cost_2026-06-*.md backups/monthly-2026-06/
   ```

4. **Adjust next month**: Update `MAX_COST_PER_MONTH_USD` if needed

---

See also:
- [orchestrator/lib/cost.py](../orchestrator/lib/cost.py) — cost calculation logic
- [orchestrator/lib/cost_gate.py](../orchestrator/lib/cost_gate.py) — enforcement + cascade
- [infra/litellm.yaml](../infra/litellm.yaml) — model routing config
- [docs/conventions.md](conventions.md) — Tier table reference
