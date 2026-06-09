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
