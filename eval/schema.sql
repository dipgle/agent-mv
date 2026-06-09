-- Extend logs/devlog.sqlite with video pipeline eval VIEWs.
-- Run: sqlite3 logs/devlog.sqlite < eval/schema.sql

-- Parse model_run events from existing `events` table.
CREATE VIEW IF NOT EXISTS model_runs AS
SELECT
    id, ts,
    actor AS role,
    ref_id AS feature_id,
    json_extract(content, '$.model') AS model,
    json_extract(content, '$.modality') AS modality,
    json_extract(content, '$.tier') AS tier,
    CAST(json_extract(content, '$.latency_ms') AS INTEGER) AS latency_ms,
    CAST(json_extract(content, '$.cost_usd') AS REAL) AS cost_usd,
    CAST(json_extract(content, '$.accepted') AS INTEGER) AS accepted,
    CAST(json_extract(content, '$.shot_idx') AS INTEGER) AS shot_idx,
    json_extract(content, '$.output_ref') AS output_ref,
    json_extract(content, '$.metrics') AS metrics_json
FROM events
WHERE kind = 'model_run';

-- Parse artifact events into per-asset rows.
CREATE VIEW IF NOT EXISTS assets AS
SELECT
    id, ts,
    ref_id AS feature_id,
    json_extract(content, '$.asset_type') AS asset_type,
    json_extract(content, '$.path') AS path,
    CAST(json_extract(content, '$.shot_idx') AS INTEGER) AS shot_idx,
    CAST(json_extract(content, '$.duration_s') AS REAL) AS duration_s,
    CAST(json_extract(content, '$.size_bytes') AS INTEGER) AS size_bytes,
    json_extract(content, '$.quality') AS quality_json
FROM events
WHERE kind = 'artifact';

-- Per-(modality, model, day) scorecard.
CREATE VIEW IF NOT EXISTS model_scores_daily AS
SELECT
    modality, model, tier,
    DATE(ts) AS day,
    COUNT(*) AS sample_n,
    AVG(CAST(accepted AS REAL)) AS pass_rate,
    AVG(latency_ms) AS avg_latency_ms,
    SUM(cost_usd) AS total_cost_usd
FROM model_runs
GROUP BY modality, model, tier, DATE(ts);

-- Render-time-per-shot trend (visual gen perf).
CREATE VIEW IF NOT EXISTS visual_render_time AS
SELECT
    DATE(ts) AS day,
    model,
    shot_idx,
    latency_ms / 1000.0 AS render_s
FROM model_runs
WHERE modality IN ('image', 'video')
ORDER BY day DESC;

-- Asset quality per modality.
CREATE VIEW IF NOT EXISTS asset_quality AS
SELECT
    feature_id,
    asset_type,
    shot_idx,
    path,
    json_extract(quality_json, '$.clip_score') AS clip_score,
    json_extract(quality_json, '$.aesthetic') AS aesthetic,
    json_extract(quality_json, '$.clip_temporal') AS clip_temporal,
    json_extract(quality_json, '$.flicker_rate') AS flicker_rate,
    json_extract(quality_json, '$.utmos') AS utmos,
    json_extract(quality_json, '$.wer_roundtrip') AS wer_roundtrip,
    json_extract(quality_json, '$.audio_sync_ms') AS audio_sync_ms,
    json_extract(quality_json, '$.pacing_variance_s') AS pacing_variance_s,
    json_extract(quality_json, '$.brand_match') AS brand_match
FROM assets;

-- Swap candidates (same shape as UIUX).
CREATE VIEW IF NOT EXISTS swap_candidates AS
SELECT
    id, ts,
    json_extract(content, '$.modality') AS modality,
    json_extract(content, '$.incumbent') AS incumbent,
    json_extract(content, '$.challenger') AS challenger,
    json_extract(content, '$.primary_metric') AS primary_metric,
    CAST(json_extract(content, '$.delta_pct') AS REAL) AS delta_pct,
    CAST(json_extract(content, '$.sample_n') AS INTEGER) AS sample_n,
    json_extract(content, '$.status') AS status,
    json_extract(content, '$.reason') AS reason
FROM events
WHERE kind = 'swap_candidate';


-- ─── Cost views (granular: cloud / compute / electricity per call) ───────
CREATE VIEW IF NOT EXISTS cost_per_video AS
SELECT
    ref_id AS feature_id,
    SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS total_usd,
    SUM(CAST(json_extract(content,'$.cost.cloud_usd') AS REAL)) AS cloud_usd,
    SUM(CAST(json_extract(content,'$.cost.compute_usd') AS REAL)) AS compute_usd,
    SUM(CAST(json_extract(content,'$.cost.electricity_usd') AS REAL)) AS electricity_usd,
    SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) FILTER
        (WHERE json_extract(content,'$.modality')='image') AS image_usd,
    SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) FILTER
        (WHERE json_extract(content,'$.modality')='video') AS video_usd,
    SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) FILTER
        (WHERE json_extract(content,'$.modality')='audio') AS audio_usd,
    SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) FILTER
        (WHERE json_extract(content,'$.modality')='text') AS text_usd,
    COUNT(*) AS n_calls
FROM events
WHERE kind='model_run' AND ref_id != ''
GROUP BY ref_id;

CREATE VIEW IF NOT EXISTS cost_per_modality_daily AS
SELECT
    DATE(ts) AS day,
    json_extract(content,'$.modality') AS modality,
    json_extract(content,'$.model') AS model,
    COUNT(*) AS n,
    SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS total_usd
FROM events
WHERE kind='model_run'
GROUP BY day, modality, model;

CREATE VIEW IF NOT EXISTS cost_trend_weekly AS
SELECT
    strftime('%Y-W%W', ts) AS week,
    COUNT(DISTINCT ref_id) FILTER (WHERE ref_id != '') AS videos,
    SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS total_usd,
    SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL))
        / NULLIF(COUNT(DISTINCT ref_id) FILTER (WHERE ref_id != ''), 0) AS avg_per_video
FROM events
WHERE kind='model_run'
GROUP BY week;


-- ─── Supervisor proposals + canary ──────────────────────────────────────
CREATE VIEW IF NOT EXISTS proposals AS
SELECT
    id, ts,
    json_extract(content,'$.id') AS proposal_id,
    json_extract(content,'$.category') AS category,
    json_extract(content,'$.priority') AS priority,
    json_extract(content,'$.title') AS title,
    json_extract(content,'$.risk') AS risk,
    json_extract(content,'$.auto_promotable') AS auto_promotable,
    CAST(json_extract(content,'$.impact.cost_per_video_delta_usd') AS REAL)
        AS cost_delta_usd,
    CAST(json_extract(content,'$.impact.latency_delta_pct') AS REAL)
        AS latency_delta_pct,
    CAST(json_extract(content,'$.impact.quality_delta_pct') AS REAL)
        AS quality_delta_pct
FROM events
WHERE kind='proposal';

CREATE VIEW IF NOT EXISTS proposals_pending AS
SELECT p.* FROM proposals p
WHERE NOT EXISTS (
    SELECT 1 FROM events d
    WHERE d.kind='proposal_decision' AND d.ref_id = p.proposal_id
);

CREATE VIEW IF NOT EXISTS proposal_decisions AS
SELECT
    ref_id AS proposal_id,
    actor AS decided_by,
    ts AS decided_at,
    json_extract(content,'$.decision') AS decision,
    json_extract(content,'$.reason') AS reason
FROM events
WHERE kind='proposal_decision';

CREATE VIEW IF NOT EXISTS canaries AS
SELECT
    ref_id AS proposal_id,
    ts,
    CAST(json_extract(content,'$.traffic_pct') AS INTEGER) AS traffic_pct,
    CAST(json_extract(content,'$.days') AS INTEGER) AS days,
    json_extract(content,'$.verdict') AS verdict,
    json_extract(content,'$.metrics') AS metrics_json
FROM events
WHERE kind='canary';


-- ─── Outcome tracking (post-publish ground truth) ───────────────────────
CREATE VIEW IF NOT EXISTS outcomes AS
SELECT
    ref_id AS feature_id,
    REPLACE(actor, 'platform:', '') AS platform,
    ts AS fetched_at,
    CAST(json_extract(content,'$.impressions') AS INTEGER) AS impressions,
    CAST(json_extract(content,'$.watch_through_pct') AS REAL) AS watch_through_pct,
    CAST(json_extract(content,'$.avg_watch_s') AS REAL) AS avg_watch_s,
    CAST(json_extract(content,'$.engagement_rate') AS REAL) AS engagement_rate,
    CAST(json_extract(content,'$.ctr') AS REAL) AS ctr,
    CAST(json_extract(content,'$.conversion_n') AS INTEGER) AS conversion_n
FROM events
WHERE kind='outcome';


-- ─── Eval tier results ──────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS eval_tier_results AS
SELECT
    REPLACE(kind, 'eval_', '') AS tier,
    actor AS evaluator,
    ref_id AS feature_id,
    ts,
    json_extract(content,'$.dimension') AS dimension,
    CAST(json_extract(content,'$.score') AS REAL) AS score,
    CAST(json_extract(content,'$.pass') AS INTEGER) AS pass
FROM events
WHERE kind IN ('eval_tier1','eval_tier2','eval_tier3');


-- ─── Cost vs Outcome (efficiency curve) ─────────────────────────────────
CREATE VIEW IF NOT EXISTS cost_vs_outcome AS
SELECT
    c.feature_id,
    c.total_usd,
    o.watch_through_pct,
    o.engagement_rate,
    o.ctr,
    -- Efficiency: outcome per dollar
    CASE WHEN c.total_usd > 0
         THEN o.watch_through_pct / c.total_usd
         ELSE NULL END AS watch_per_dollar
FROM cost_per_video c
LEFT JOIN outcomes o ON c.feature_id = o.feature_id;


-- ─── Tier 4 / Calibration loop views ────────────────────────────────────
CREATE VIEW IF NOT EXISTS publish_records AS
SELECT
    ref_id AS feature_id, ts AS recorded_at,
    json_extract(content,'$.platforms') AS platforms_json
FROM events
WHERE kind='publish_record';

CREATE VIEW IF NOT EXISTS outcomes_latest AS
SELECT
    feature_id,
    platform,
    MAX(fetched_at) AS fetched_at,
    impressions, watch_through_pct, avg_watch_s,
    engagement_rate, ctr, conversion_n
FROM outcomes
GROUP BY feature_id, platform;

CREATE VIEW IF NOT EXISTS outcomes_summary AS
SELECT
    feature_id,
    COUNT(DISTINCT platform) AS platforms_n,
    AVG(watch_through_pct) AS avg_watch_through,
    SUM(impressions) AS total_impressions,
    AVG(engagement_rate) AS avg_engagement,
    SUM(conversion_n) AS total_conversions,
    MAX(fetched_at) AS last_fetched
FROM outcomes
GROUP BY feature_id;

CREATE VIEW IF NOT EXISTS panel_calibrations AS
SELECT
    ts, ref_id AS calibrated_for,
    json_extract(content,'$.decision') AS decision,
    CAST(json_extract(content,'$.r2') AS REAL) AS r2,
    CAST(json_extract(content,'$.sample_n') AS INTEGER) AS sample_n,
    json_extract(content,'$.weights') AS weights_json,
    json_extract(content,'$.reason') AS reason
FROM events
WHERE kind='panel_calibration';

CREATE VIEW IF NOT EXISTS hook_calibrations AS
SELECT
    ts, ref_id AS calibrated_for,
    json_extract(content,'$.decision') AS decision,
    CAST(json_extract(content,'$.r2') AS REAL) AS r2,
    CAST(json_extract(content,'$.sample_n') AS INTEGER) AS sample_n,
    json_extract(content,'$.weights') AS weights_json,
    json_extract(content,'$.reason') AS reason
FROM events
WHERE kind='hook_calibration';

CREATE VIEW IF NOT EXISTS champions_evolution AS
SELECT
    ts, ref_id AS evolved_at,
    CAST(json_extract(content,'$.champions_n') AS INTEGER) AS champions_n,
    CAST(json_extract(content,'$.anti_patterns_n') AS INTEGER) AS anti_patterns_n,
    CAST(json_extract(content,'$.candidates_n') AS INTEGER) AS candidates_n,
    json_extract(content,'$.window_days') AS window_days,
    json_extract(content,'$.note') AS note
FROM events
WHERE kind='champion_evolve';


-- ─── Config mutations (auto-promote write-through log) ───────────────────────
-- Covers both litellm.yaml swaps and workflow JSON replacements.
-- Also surfaces rollback events so operators can trace the full mutation history.
CREATE VIEW IF NOT EXISTS config_mutations AS
SELECT
    id,
    ts,
    ref_id AS proposal_id,
    -- Distinguish promotion vs rollback vs snapshot events.
    kind AS event_kind,
    json_extract(content, '$.target')            AS target_file,
    -- For litellm.yaml mutations: route-level diff fields.
    json_extract(content, '$.diff.route_name')   AS route_name,
    json_extract(content, '$.diff.old_model')    AS old_model,
    json_extract(content, '$.diff.new_model')    AS new_model,
    json_extract(content, '$.diff.new_api_base') AS new_api_base,
    -- For workflow swaps.
    json_extract(content, '$.diff.workflow')     AS workflow_name,
    json_extract(content, '$.diff.backed_up_to') AS workflow_backup,
    -- For rollbacks: which snapshot was restored.
    json_extract(content, '$.snapshot_dir')      AS snapshot_dir,
    -- Full diff JSON for cases not covered by the named columns above.
    json_extract(content, '$.diff')              AS diff_json,
    -- Optional: promote event tag stored by auto_promote.py.
    json_extract(content, '$.event')             AS promote_event
FROM events
WHERE kind IN ('config_mutation', 'config_rollback', 'config_snapshot',
               'manual_rollback', 'auto_promote_failed')
ORDER BY ts DESC;


-- ─── Panel reliability views (added 2026-06-09) ─────────────────────────────

-- Timeout count per (model, calendar day).
CREATE VIEW IF NOT EXISTS panel_timeouts AS
SELECT
    json_extract(content, '$.role') AS model,
    DATE(ts)                         AS day,
    COUNT(*)                         AS timeout_count
FROM events
WHERE kind = 'panel_timeout'
GROUP BY json_extract(content, '$.role'), DATE(ts);

-- Latest breaker state per model: open if the most recent breaker-skip event
-- is newer than EVAL_CB_OPEN_DURATION_S (300 s default).  We surface a boolean
-- approximation here; the authoritative state lives in eval/breakers.json.
CREATE VIEW IF NOT EXISTS panel_breaker_state AS
SELECT
    json_extract(content, '$.model') AS model,
    MAX(ts)                           AS last_skip_at,
    -- Treat as open when the most recent skip event was within the last 5 min.
    CASE
        WHEN (strftime('%s', 'now') - strftime('%s', MAX(ts))) < 300
        THEN 'open'
        ELSE 'closed'
    END AS breaker_state
FROM events
WHERE kind = 'panel_breaker_skip'
GROUP BY json_extract(content, '$.model');

-- Features that ran with a partial panel (2+ votes but not full complement).
CREATE VIEW IF NOT EXISTS panel_partial_count AS
SELECT
    DATE(ts)  AS day,
    COUNT(*)  AS partial_runs,
    -- How many of those partial runs produced at least 1 vote.
    SUM(CASE WHEN json_extract(content, '$.responded') > 0 THEN 1 ELSE 0 END)
              AS runs_with_votes
FROM events
WHERE kind = 'panel_partial'
GROUP BY DATE(ts);


-- ─── Web Chat Router (Tier W) ────────────────────────────────────────────────
-- Events logged by mcp/web-chat-router (kind='web_chat_call').
-- Use this view for quota monitoring and latency tracking of free-tier votes.
-- Apply: sqlite3 logs/devlog.sqlite < eval/schema.sql
CREATE VIEW IF NOT EXISTS web_chat_calls AS
SELECT
    id,
    ts,
    json_extract(content, '$.provider')             AS provider,
    json_extract(content, '$.prompt_hash')           AS prompt_hash,
    CAST(json_extract(content, '$.latency_ms')   AS INTEGER) AS latency_ms,
    CAST(json_extract(content, '$.response_len') AS INTEGER) AS response_len,
    json_extract(content, '$.model')                 AS model,
    CAST(json_extract(content, '$.blocked') AS INTEGER)      AS blocked,
    json_extract(content, '$.error')                 AS error
FROM events
WHERE kind = 'web_chat_call';

-- Per-provider call counts in the current hour (quota monitoring).
CREATE VIEW IF NOT EXISTS web_chat_quota_live AS
SELECT
    json_extract(content, '$.provider') AS provider,
    COUNT(*) AS calls_this_hour
FROM events
WHERE kind = 'web_chat_call'
  AND datetime(ts) >= datetime('now', '-1 hour')
  AND CAST(json_extract(content, '$.blocked') AS INTEGER) = 0
GROUP BY provider;
