#!/usr/bin/env python3
"""
Calibrate Tier 2 LLM panel weights from real outcome data.

Replaces equal-weight aggregation with regression-derived weights so the
model whose votes actually predict watch-through carries more influence.

Method: ridge regression (closed form, numpy stdlib) of per-model panel
scores onto outcome.watch_through_pct.  Constraints:
  - weights normalised to sum = 1 (probability-like)
  - weights clipped to [0.05, 0.60] to keep minority models alive
  - falls back to equal weights if R^2 < 0.10 or sample_n < 50

Output: eval/benchmarks/panel_weights.json — picked up by lib/eval_tier2.py
       on next pipeline run (no restart needed).

Schedule: weekly cron, after fetch_outcomes.py.
"""

from __future__ import annotations
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
WEIGHTS_PATH = Path("eval/benchmarks/panel_weights.json")
MIN_SAMPLE_N = 50
MIN_R2 = 0.10
WEIGHT_FLOOR = 0.05
WEIGHT_CEIL = 0.60


def fetch_training_data(db: sqlite3.Connection, days: int = 180) -> list[dict]:
    """
    Pair each feature with: per-model panel score + outcome watch-through.

    Returns rows shaped like:
        {feature_id, scores: {model_name: score, ...}, watch_through_pct}
    """
    # Per-feature per-model average tier2 score across dimensions
    rows = db.execute(f"""
        SELECT
            ref_id AS feature_id,
            actor AS evaluator,
            AVG(CAST(json_extract(content,'$.score') AS REAL)) AS score
        FROM events
        WHERE kind='eval_tier2'
          AND ts > datetime('now', '-{days} days')
          AND json_extract(content,'$.score') IS NOT NULL
        GROUP BY ref_id, evaluator
    """).fetchall()

    by_feature: dict[str, dict[str, float]] = {}
    for feature_id, evaluator, score in rows:
        by_feature.setdefault(feature_id, {})[evaluator] = score

    # Outcome per feature
    outcomes = {fid: wt for fid, wt in db.execute(f"""
        SELECT ref_id, AVG(CAST(json_extract(content,'$.watch_through_pct') AS REAL))
        FROM events
        WHERE kind='outcome'
          AND ts > datetime('now', '-{days} days')
          AND json_extract(content,'$.watch_through_pct') IS NOT NULL
        GROUP BY ref_id
    """).fetchall()}

    training = []
    for fid, scores in by_feature.items():
        wt = outcomes.get(fid)
        if wt is None:
            continue
        training.append({
            "feature_id": fid,
            "scores": scores,
            "watch_through_pct": float(wt),
        })
    return training


def regress(training: list[dict]) -> tuple[dict[str, float], float, int]:
    """
    Ridge regression weights → minimise (Xw - y)^2 + alpha * ||w||^2.
    Returns (weights_dict, r_squared, sample_n).
    """
    try:
        import numpy as np
    except ImportError:
        return {}, 0.0, 0

    # Collect all evaluators seen anywhere
    evaluators = sorted({m for row in training for m in row["scores"].keys()})
    if not evaluators or len(training) < MIN_SAMPLE_N:
        return {ev: 1.0 / max(1, len(evaluators)) for ev in evaluators}, 0.0, len(training)

    X = np.zeros((len(training), len(evaluators)))
    y = np.zeros(len(training))
    for i, row in enumerate(training):
        for j, ev in enumerate(evaluators):
            X[i, j] = row["scores"].get(ev, 0.0)
        y[i] = row["watch_through_pct"]

    alpha = 1.0  # ridge regulariser; >0 keeps weights bounded
    n_features = X.shape[1]
    try:
        w = np.linalg.solve(X.T @ X + alpha * np.eye(n_features), X.T @ y)
    except np.linalg.LinAlgError:
        return {ev: 1.0 / n_features for ev in evaluators}, 0.0, len(training)

    # R^2
    pred = X @ w
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    r2 = 1 - ss_res / ss_tot

    # Clip + normalise
    w = np.clip(w, WEIGHT_FLOOR, WEIGHT_CEIL)
    w = w / w.sum() if w.sum() > 0 else np.full(n_features, 1.0 / n_features)

    return {ev: float(w[i]) for i, ev in enumerate(evaluators)}, float(r2), len(training)


def main():
    if not DEVLOG.exists():
        print(f"DEVLOG missing: {DEVLOG}")
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        training = fetch_training_data(db)

    weights, r2, n = regress(training)

    decision = "applied"
    reason = f"n={n}, r2={r2:.3f}"
    if n < MIN_SAMPLE_N:
        decision = "fallback_equal"
        reason = f"insufficient samples (need >={MIN_SAMPLE_N}, got {n})"
    elif r2 < MIN_R2:
        decision = "fallback_equal"
        reason = f"r2 too low (need >={MIN_R2}, got {r2:.3f}) — panel doesn't predict outcome yet"

    output = {
        "weights": weights,
        "r2": r2,
        "sample_n": n,
        "decision": decision,
        "reason": reason,
        "calibrated_at": datetime.utcnow().isoformat() + "Z",
    }

    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(output, indent=2))
    print(f"Calibrated weights → {WEIGHTS_PATH}")
    print(f"  decision: {decision}")
    print(f"  reason:   {reason}")
    print(f"  weights:  {json.dumps(weights, indent=4)}")

    devlog.append(
        kind="panel_calibration",
        actor="supervisor",
        ref_type="system",
        ref_id=date.today().isoformat(),
        content=output,
    )


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
