#!/usr/bin/env python3
"""
Calibrate hook signal weights from real outcome.

Hook is the single most predictive dimension of watch-through (0.7-0.85
correlation in social benchmarks).  This script regresses the six hook
signals onto outcome.watch_through_pct so the weights in eval_hook.py
reflect what actually predicts retention for *our* channel/audience —
not a one-size-fits-all default.

Signals (from lib/eval_hook.py):
    motion_0_1s, scene_cuts_0_2s, face_0_05s,
    text_overlay_0_05s, audio_ramp_0_1s, voice_0_1s

Output: eval/benchmarks/hook_weights.json
"""

from __future__ import annotations
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
WEIGHTS_PATH = Path("eval/benchmarks/hook_weights.json")
SIGNALS = [
    "motion_0_1s",
    "scene_cuts_0_2s",
    "face_0_05s",
    "text_overlay_0_05s",
    "audio_ramp_0_1s",
    "voice_0_1s",
]
MIN_SAMPLE_N = 30
MIN_R2 = 0.10
WEIGHT_FLOOR = 0.05


def fetch_training_data(db: sqlite3.Connection, days: int = 180) -> list[dict]:
    """Pair each feature's hook signals with its watch-through outcome."""
    # Per-feature hook signal values
    rows = db.execute(f"""
        SELECT
            ref_id,
            json_extract(content,'$.signals') AS signals
        FROM events
        WHERE kind='eval_tier1'
          AND json_extract(content,'$.dimension')='hook'
          AND ts > datetime('now', '-{days} days')
    """).fetchall()

    by_feature: dict[str, dict] = {}
    for fid, signals_json in rows:
        if not signals_json:
            continue
        try:
            by_feature[fid] = json.loads(signals_json)
        except Exception:
            continue

    # Outcome per feature
    outcomes = {}
    for fid, wt in db.execute(f"""
        SELECT ref_id, AVG(CAST(json_extract(content,'$.watch_through_pct') AS REAL))
        FROM events
        WHERE kind='outcome'
          AND ts > datetime('now', '-{days} days')
          AND json_extract(content,'$.watch_through_pct') IS NOT NULL
        GROUP BY ref_id
    """).fetchall():
        outcomes[fid] = float(wt)

    training = []
    for fid, signals in by_feature.items():
        wt = outcomes.get(fid)
        if wt is None:
            continue
        training.append({
            "feature_id": fid,
            "signals": signals,
            "watch_through_pct": wt,
        })
    return training


def regress(training: list[dict]) -> tuple[dict[str, float], float, int]:
    try:
        import numpy as np
    except ImportError:
        return {}, 0.0, 0

    n = len(training)
    if n < MIN_SAMPLE_N:
        return {s: 1.0 / len(SIGNALS) for s in SIGNALS}, 0.0, n

    X = np.zeros((n, len(SIGNALS)))
    y = np.zeros(n)
    for i, row in enumerate(training):
        for j, sig in enumerate(SIGNALS):
            X[i, j] = float(row["signals"].get(sig, 0.0))
        y[i] = row["watch_through_pct"]

    alpha = 1.0
    try:
        w = np.linalg.solve(X.T @ X + alpha * np.eye(len(SIGNALS)), X.T @ y)
    except np.linalg.LinAlgError:
        return {s: 1.0 / len(SIGNALS) for s in SIGNALS}, 0.0, n

    pred = X @ w
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    r2 = 1 - ss_res / ss_tot

    # Floor to keep all signals alive, then normalise
    w = np.clip(w, WEIGHT_FLOOR, None)
    w = w / w.sum() if w.sum() > 0 else np.full(len(SIGNALS), 1.0 / len(SIGNALS))

    return {SIGNALS[i]: float(w[i]) for i in range(len(SIGNALS))}, float(r2), n


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
        reason = f"r2 too low ({r2:.3f}) — signals don't predict outcome yet"

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
    print(f"Hook weights -> {WEIGHTS_PATH}")
    print(f"  decision: {decision}")
    print(f"  reason:   {reason}")
    for sig, w in sorted(weights.items(), key=lambda x: -x[1]):
        print(f"  {sig:24s} = {w:.3f}")

    devlog.append(
        kind="hook_calibration",
        actor="supervisor",
        ref_type="system",
        ref_id=date.today().isoformat(),
        content=output,
    )


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
