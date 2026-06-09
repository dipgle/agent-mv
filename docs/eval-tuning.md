# Evaluation & Calibration Tuning Guide

This guide explains how to use the evaluation pipeline for continuous improvement: baseline snapshots, outcome ingestion, and champion evolution.

## Overview: Closed-Loop Calibration

The evaluation system closes a feedback loop:

```
Video render
  ↓
[Compare against golden baseline]
  ↓
[Collect outcome metrics (YouTube watch%, TikTok completion, etc.)]
  ↓
[Calibrate panel: adjust model weights based on outcome correlation]
  ↓
[Champion evolution: promote models that predict success]
  ↓
[Next render uses improved panel]
```

## Part 1: Regression Baseline (Snapshot)

**Purpose:** Establish a frozen baseline of evaluation scores per (evaluator, dimension) to detect quality drift.

**Status:** Shipped 2026-06-09. Embedded in daily cron after audit.py.

### Creating a Baseline Snapshot

The regression check system maintains a baseline of mean/stddev for every eval dimension (e.g., `qwen2.5-vl::aesthetic`, `r1::narrative`). Create the initial snapshot from 30 days of historical eval data:

```bash
# Create golden baseline from last 30 days
python orchestrator/supervisor/regression_check.py snapshot

# Output: eval/golden_regression/baseline.json (+ timestamped archive)
```

This snapshot collects all `eval_tier2` and `eval_tier3` events from the past month, groups by (evaluator, dimension), and computes mean/stddev.

Expected output structure (`eval/golden_regression/baseline.json`):

```json
{
  "snapshot_at": "2026-06-09",
  "baseline": {
    "qwen2.5-vl::aesthetic": {
      "mean": 0.78,
      "stddev": 0.08,
      "n": 42
    },
    "r1::narrative": {
      "mean": 0.85,
      "stddev": 0.06,
      "n": 38
    }
  }
}
```

### Running Daily Regression Checks

The daily cron (02:00 local) automatically calls:

```bash
python orchestrator/supervisor/regression_check.py check
```

This compares **last 7 days** of evals vs the baseline. Any dimension that dropped >5% triggers a `regression_detected` event.

Example output in `eval/reports/regression_2026-06-09.md`:

```markdown
# Regression Check — 2026-06-09

Baseline: 2026-06-01
Check window: last 7 days
Threshold: >5% drop = regression

## Regressions detected: 1

| Key | Baseline | Recent | Drop % | N |
|---|---|---|---|---|
| `qwen2.5-vl::aesthetic` | 0.78 | 0.73 | 6.41% | 8 |
```

Exit code: 0 (healthy), 2 (regression detected, alert ops).

### Re-snapshotting

Re-snapshot when:
1. You significantly improve evaluator quality (e.g., upgrade Qwen → Qwen 3)
2. You recalibrate panel weights (outcome calibration)
3. You want to ignore old "bad" data and reset baseline

```bash
# Force overwrite even if baseline <7 days old
python orchestrator/supervisor/regression_check.py snapshot --force
```

### Listing Snapshots

View all baseline archives:

```bash
python orchestrator/supervisor/regression_check.py list-baselines

# Output example:
# baseline_2026-06-01.json  2026-06-01 (keys=18)
# baseline_2026-06-09.json  2026-06-09 (keys=19)
# baseline.json (current)   2026-06-09 (symlink)
```

### Querying Results

View regression findings via SQL:

```bash
sqlite3 logs/devlog.sqlite < eval/schema.sql

# Then query the regression_findings VIEW:
SELECT * FROM regression_findings WHERE drop_pct > 5 ORDER BY detected_at DESC;
    --intent "..." \
    --feature-id "..." \
    --regression-baseline eval/golden_regression/baseline-rtx4090.json
```

## Part 2: Outcome Ingestion

**Purpose:** Feed real-world video performance back into the system.

**Status:** Partially shipped (schema exists, API client stubs planned).

### Publishing Outcome Events

When a video goes live on YouTube, TikTok, or Meta, you collect metrics. Publish them as devlog events:

```bash
# After 1 day of YouTube metrics
python -c "
import sqlite3
from datetime import datetime

conn = sqlite3.connect('logs/devlog.sqlite')
cur = conn.cursor()

outcome = {
    'video_id': 'VID-001',
    'platform': 'youtube',
    'publish_ts': '2026-06-09T10:00:00Z',
    'views': 15000,
    'watch_time_sec': 180000,          # total seconds watched
    'avg_watch_pct': 78,                # avg % watched
    'completion_rate': 0.62,            # % that watched to end
    'click_through_rate': 0.08,         # if CTA present
    'metadata': {
        'render_cost': 12.50,
        'planner_model': 'qwen3:32b',
        'reviewer_model': 'deepseek-r1:14b',
        'keyframe_model': 'flux-dev',
        'motion_model': 'ltx-video'
    }
}

# Insert outcome event
cur.execute('''
    INSERT INTO events 
    (ts, kind, actor, ref_id, content, metadata)
    VALUES (?, 'outcome', 'platform_ingest', ?, ?, ?)
''', (
    datetime.utcnow().isoformat(),
    'VID-001',
    f'outcome_{outcome[\"platform\"]}',
    json.dumps(outcome)
))

conn.commit()
print('Outcome recorded:', outcome)
"
```

### Devlog Outcome Schema

After `eval/schema.sql` is applied, a VIEW `v_outcomes` lets you query:

```sql
SELECT
  video_id,
  platform,
  views,
  avg_watch_pct,
  completion_rate,
  json_extract(metadata, '$.planner_model') AS planner_model,
  json_extract(metadata, '$.render_cost') AS render_cost
FROM v_outcomes
WHERE publish_ts >= date('now', '-7 days')
ORDER BY views DESC;
```

Example query: Find high-performing videos by planner model:

```sql
SELECT
  json_extract(metadata, '$.planner_model') AS model,
  COUNT(*) AS video_count,
  ROUND(AVG(completion_rate), 3) AS avg_completion,
  ROUND(SUM(views), 0) AS total_views
FROM v_outcomes
WHERE publish_ts >= date('now', '-30 days')
GROUP BY model
ORDER BY avg_completion DESC;

-- Output:
-- qwen3:32b    | 8 | 0.71 | 120000
-- deepseek-r1  | 5 | 0.65 | 85000
-- claude-opus  | 3 | 0.58 | 42000
```

### Outcome Ingestion Workflow (Planned)

Eventually, a scheduled script will pull metrics from platform APIs:

```bash
# (Planned in Phase 4–5)
# orchestrator/supervisor/ingest_outcomes.py
#
# Daily job: fetch YouTube Analytics → Outcome events
#   - Loop over all `videos` table where status='published'
#   - Query YouTube Data API for views/watch_time/completion
#   - Publish as devlog `kind=outcome` events
#   - Calculate correlation: planner_model → completion_rate
#
# Weekly job: summarize correlation scores
#   - Rank models by success prediction
#   - Flag models underperforming baseline
```

## Part 3: Calibration Loop (Panel)

**Purpose:** Adjust model selection weights based on outcome correlation.

**Status:** Shipped (Phase 0.4 outcome loop). This documents how to use it.

### How Calibration Works

The panel is a **weighted ensemble** of models voting on quality. Weights adjust based on accuracy:

```
[Reviewer ensemble: 3 models vote on quality of VID-001]
  Model A (qwen3:8b):     Score 82 / 100
  Model B (deepseek-r1):  Score 78 / 100
  Model C (claude-sonnet): Score 85 / 100

[Later, VID-001 outcomes arrive]
  YouTube completion rate: 71% (above average!)

[Calibrate: which model's opinion was most predictive?]
  Model A → predicted 82, actual-signal 71 → error = 11 (good, close)
  Model B → predicted 78, actual-signal 71 → error = 7 (better!)
  Model C → predicted 85, actual-signal 71 → error = 14 (worst)

[Update weights for next render]
  Model B weight ↑ (more predictive)
  Model C weight ↓ (overoptimistic)
```

The devlog tracks these calibration updates in `v_panel_calibration`:

```sql
SELECT
  ts,
  model_name,
  accuracy_score,
  weight_before,
  weight_after,
  n_observations
FROM v_panel_calibration
ORDER BY ts DESC;

-- Output:
-- 2026-06-09 14:32:01 | deepseek-r1  | 0.92 | 0.30 | 0.35 | 42
-- 2026-06-09 14:32:01 | claude-sonnet| 0.78 | 0.40 | 0.35 | 42
-- 2026-06-09 14:32:01 | qwen3:8b     | 0.85 | 0.30 | 0.30 | 42
```

### When Calibration Triggers

Calibration runs:

1. **Per outcome batch** (when new outcome events arrive)
   - If n ≥ 50: strong signal, weights shift 5–10%
   - If n ≥ 30: medium signal, weights shift 2–5%
   - If n < 30: weak signal, no change yet

2. **Monitored via supervisor**
   - `supervisor/auto_promote.py` checks calibration signals weekly
   - If accuracy > 0.85, model becomes "champion candidate"
   - If accuracy < 0.70 for 3 weeks, model demoted (fallback)

### Inspecting Calibration State

```bash
# View current panel calibration
sqlite3 logs/devlog.sqlite << 'SQL'
SELECT
  model_name,
  ROUND(weight, 3) AS current_weight,
  ROUND(accuracy_score, 3) AS accuracy,
  ROUND(n_observations, 0) AS n_obs,
  status
FROM v_panel_state
ORDER BY weight DESC;
SQL

-- Output:
-- deepseek-r1   | 0.380 | 0.887 | 42 | champion
-- qwen3:8b      | 0.310 | 0.814 | 42 | active
-- claude-sonnet | 0.310 | 0.756 | 42 | active
```

### Manual Calibration Adjustment

If you suspect calibration is wrong (e.g., YouTube metrics lag), override weights:

```bash
# Edit and reload calibration state (planned UI)
# For now, edit devlog directly (dangerous!):

sqlite3 logs/devlog.sqlite << 'SQL'
UPDATE panel_weights
SET weight = 0.50,
    updated_ts = datetime('now')
WHERE model_name = 'deepseek-r1';
SQL

# Log the override decision
python -c "
from orchestrator.lib.devlog import log_event
log_event(
    kind='decision',
    actor='human',
    content='Manual calibration: deepseek-r1 weight → 0.50 (was 0.38) due to YouTube metrics lag'
)
"
```

## Part 4: Champion Evolution

**Purpose:** Promote top-performing models to higher-impact roles.

**Status:** Shipped (Phase 0.4). This documents how to use it.

### Champion Promotion Workflow

```
Baseline: All models equal weight in Reviewer panel (0.33 each)

Week 1:
  deepseek-r1 accuracy: 0.92 (top performer!)
  → Candidate for Planner role (currently qwen3:32b)

Week 2:
  deepseek-r1 accuracy: 0.89 (still strong)
  → Champion signal: 2 weeks at 0.85+

Week 3:
  Supervisor auto_promote.py detects champion
  → Proposal: "Promote deepseek-r1 → Planner model (expected cost +0.15/call, but +5% quality)"

Week 4 (Canary phase):
  10% of Planner calls route to deepseek-r1 instead of qwen3:32b
  → Monitor: cost, latency, critique quality

Week 5 (Promotion decision):
  If canary metrics good: commit promotion
    qwen3:32b → fallback (kept for OOM)
    deepseek-r1 → primary Planner
```

### Inspecting Champion State

```sql
SELECT
  proposal_id,
  proposal_title,
  model_name,
  current_tier,
  proposed_tier,
  canary_status,
  accuracy_signal,
  expected_cost_delta,
  decision_ts
FROM v_champion_proposals
WHERE decision_ts >= date('now', '-30 days')
ORDER BY decision_ts DESC;

-- Output:
-- PROP-001 | Promote deepseek-r1 → Planner | deepseek-r1 | B | A | canary | 0.89 | +0.15 | 2026-06-02
-- PROP-002 | Demote claude-sonnet (low accuracy) | claude-sonnet | A | B | rejected | 0.68 | −0.20 | 2026-06-01
```

### Canary Deployment

When a proposal enters canary phase:

```bash
# Check canary config
cat eval/canary/proposal_PROP-001.json

# Output:
{
  "proposal_id": "PROP-001",
  "model": "deepseek-r1",
  "role": "planner",
  "canary_pct": 0.10,
  "live_since": "2026-06-02T09:00:00Z",
  "metrics": {
    "calls": 47,
    "avg_latency_sec": 42.5,
    "error_rate": 0.02,
    "quality_score_vs_baseline": 0.98
  }
}
```

Metrics to watch during canary:

| Metric | Baseline | Good Canary | Rollback if |
|---|---|---|---|
| Latency | 35s | <45s | >60s |
| Error rate | 2% | <5% | >8% |
| Quality vs baseline | — | ≥0.95 | <0.90 |
| Cost delta | — | +10% acceptable | >+20% |

If all good after 1 week, promote:

```bash
# (Planned auto-promotion)
# supervisor/auto_promote.py detects all metrics good
# → update infra/litellm.yaml
# → log decision: "Champion promotion approved: deepseek-r1 → Planner"
# → remove canary config
```

If bad metrics, rollback:

```bash
# Manual rollback (if auto-promotion hangs)
python -c "
from orchestrator.lib.devlog import log_event
log_event(
    kind='decision',
    actor='supervisor',
    content='Champion rollback: PROP-001 deepseek-r1 Planner canary rejected (error_rate 8.5% > 8%)'
)
"

# Edit infra/litellm.yaml to restore qwen3:32b as primary
```

## Integration: How It All Connects

### Daily Supervisor Loop

```bash
# orchestrator/cron/daily.sh runs:

# 1. audit.py → detect bottleneck, regression, waste
python orchestrator/supervisor/audit.py

# 2. cost_rollup.py → summarize spend per modality/model
python orchestrator/supervisor/cost_rollup.py

# 3. Ingest outcomes from YouTube/TikTok/Meta (planned)
# python orchestrator/supervisor/ingest_outcomes.py

# 4. Calibrate panel weights based on new outcomes (planned)
# python orchestrator/supervisor/calibrate_panel.py

# 5. auto_promote.py → detect champions, start canary if ready
python orchestrator/supervisor/auto_promote.py
```

### Weekly Supervisor Loop

```bash
# orchestrator/cron/weekly.sh runs:

# 1. scan.py → external scan HF/arxiv/pricing
python orchestrator/supervisor/scan.py

# 2. propose.py → LLM-generated improvement proposals
python orchestrator/supervisor/propose.py

# 3. Champion promotion review (part of auto_promote logic)
# Outputs: proposal_*.json for human or auto-approval
```

### Dashboard Integration

The dashboard (`eval/dashboard.html`) displays:

- **Audit tab**: Bottleneck (slowest modality), Regression (drift alerts), Waste (duplicate calls)
- **Cost tab**: Daily spend, cost per modality, top cost models
- **Proposals tab**: Active proposals, canary metrics, champion candidates
- **Champions tab**: Current panel weights, accuracy per model, promotion history

## Practical Example: End-to-End Calibration

**Scenario:** You render 5 videos over 1 week, publish to YouTube, and want to improve Planner quality.

### Day 1–5: Render Videos
```bash
for i in 1 2 3 4 5; do
  python orchestrator/pipeline.py \
    --intent "..." \
    --feature-id "VID-00$i"
done
```

Each render logs to devlog. Reviewer scores: [82, 79, 85, 80, 83] (avg 82).

### Day 6–12: Outcomes Arrive
YouTube gives real metrics. Publish as devlog events:
- VID-001: 71% completion (low, despite Reviewer score 82)
- VID-002: 68% completion (Reviewer score 79 → good prediction!)
- VID-003: 85% completion (very high! Reviewer score 85 → excellent)
- VID-004: 73% completion (decent)
- VID-005: 79% completion (good)

### Day 13: Calibration Runs
Supervisor detects pattern: Reviewer scores correlate best when prediction = actual ± 10%.

Models in ensemble:
- qwen3:8b: avg error 15% (bad)
- deepseek-r1: avg error 6% (good) ← Consider promoting
- claude-sonnet: avg error 12% (okay)

### Day 14: Proposal Generated
```
PROP-003: Promote deepseek-r1 to Planner (currently qwen3:32b-thinking)
  - Reasoning: Reviewer panel calibration shows deepseek-r1 0.88 accuracy
  - Cost: +$0.25/video (44s latency vs 35s for qwen3:32b)
  - Signal: n=5 videos (weak, but consistent)
  - Recommendation: Start 2-week canary on 25% of Planner calls
```

### Day 15: Canary Starts
`infra/litellm.yaml` updated: Planner 25% deepseek-r1, 75% qwen3:32b

Track canary metrics daily.

### Day 28: Promotion Decision
If canary shows accuracy > 0.85 + cost increase acceptable:
```
✓ APPROVE: Promote deepseek-r1 to Planner primary
  - qwen3:32b moved to fallback (OOM recovery)
  - Expected: +5% script quality, +$0.25/video cost
```

Next batch of videos uses improved Planner. Outcomes loop continues.

---

## Reference

- [eval/schema.sql](../eval/schema.sql) — VIEWs: `v_outcomes`, `v_panel_calibration`, `v_champion_proposals`
- [orchestrator/supervisor/auto_promote.py](../orchestrator/supervisor/auto_promote.py) — Champion promotion logic
- [eval/serve.py](../eval/serve.py) — HTTP API for dashboard
- [eval/dashboard.html](../eval/dashboard.html) — UI for calibration metrics
