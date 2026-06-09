"""
Cost gate — hard budget cap per video + cascade fallback.

Used inline by pipeline.py before each model call:

    model = cost_gate.gate(feature_id, intended="executor", est_cost=0.05)
    # ^ may return a downgraded model name if budget tight

Configurable via env:
    MAX_COST_PER_VIDEO_USD     (default 5.00)
    MAX_COST_PER_DAY_USD       (default 50.00)
    MAX_COST_PER_MONTH_USD     (default 500.00)
"""

from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from typing import Optional

from . import devlog

DEVLOG_PATH = Path("logs/devlog.sqlite")

MAX_PER_VIDEO = float(os.environ.get("MAX_COST_PER_VIDEO_USD", "5.00"))
MAX_PER_DAY = float(os.environ.get("MAX_COST_PER_DAY_USD", "50.00"))
MAX_PER_MONTH = float(os.environ.get("MAX_COST_PER_MONTH_USD", "500.00"))


# Cascade order: each key maps to its cheaper fallback (or None if no further fallback)
COST_CASCADE: dict[str, str | None] = {
    # Text LLM cascade (high → low)
    "adjudicator":               "reviewer-paid",
    "reviewer-paid":             "reviewer",
    "reviewer":                  None,   # local — cannot downgrade further

    "executor-paid":             "executor-fallback-fast",
    "executor-fallback-fast":    "executor",
    "executor":                  None,

    "planner-script-hard":       "planner",
    "planner":                   None,

    "researcher-bulk":           "researcher-text",
    "researcher-text":           None,

    # Visual gen cascade
    "runway-gen-3":              "wan-2.1-14b",
    "wan-2.1-14b":               "ltx-video",
    "ltx-video":                 "ltx-video-q4",
    "ltx-video-q4":              None,

    "flux.1-pro":                "flux.1-dev",
    "flux.1-dev":                "flux.1-schnell",
    "flux.1-schnell":            None,

    # Audio
    "elevenlabs-tts":            "f5-tts",
    "f5-tts":                    None,
    "suno-v4":                   "stable-audio-open",
    "stable-audio-open":         None,
}


class BudgetExceeded(Exception):
    """Raised when no cheaper fallback exists and budget is blown."""
    pass


def _spent_per_video(feature_id: str) -> float:
    with sqlite3.connect(DEVLOG_PATH) as db:
        row = db.execute(
            """SELECT COALESCE(SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)), 0)
               FROM events
               WHERE kind='model_run' AND ref_id=?""",
            (feature_id,)
        ).fetchone()
    return float(row[0] or 0)


def _spent_today() -> float:
    with sqlite3.connect(DEVLOG_PATH) as db:
        row = db.execute(
            """SELECT COALESCE(SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)), 0)
               FROM events
               WHERE kind='model_run' AND DATE(ts)=DATE('now')"""
        ).fetchone()
    return float(row[0] or 0)


def _spent_this_month() -> float:
    with sqlite3.connect(DEVLOG_PATH) as db:
        row = db.execute(
            """SELECT COALESCE(SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)), 0)
               FROM events
               WHERE kind='model_run' AND strftime('%Y-%m', ts)=strftime('%Y-%m','now')"""
        ).fetchone()
    return float(row[0] or 0)


def gate(
    feature_id: str,
    intended: str,
    est_cost: float = 0.0,
    *,
    allow_local_only: bool = False,
) -> str:
    """
    Return the model name to actually use (possibly downgraded).

    Args:
        feature_id:        current video being rendered
        intended:          logical model name from litellm.yaml
        est_cost:          rough estimate $; 0 means "we don't know"
        allow_local_only:  force local tier B (skip all cloud), e.g. on offline mode

    Raises:
        BudgetExceeded when no cheaper fallback exists.
    """
    if allow_local_only:
        # Walk cascade until we hit a non-cloud variant
        m = intended
        while m and not _is_local(m):
            m = COST_CASCADE.get(m)
        if m is None:
            raise BudgetExceeded(f"{intended}: no local fallback exists")
        return m

    spent_video = _spent_per_video(feature_id)
    spent_day = _spent_today()
    spent_month = _spent_this_month()

    headroom = min(
        MAX_PER_VIDEO - spent_video,
        MAX_PER_DAY - spent_day,
        MAX_PER_MONTH - spent_month,
    )

    if est_cost <= headroom:
        return intended  # within budget

    # Try cascade
    current = intended
    while current and est_cost > headroom:
        cheaper = COST_CASCADE.get(current)
        if cheaper is None:
            # No fallback — hard fail
            devlog.log_decision(
                "cost_gate", feature_id,
                decision=f"reject_{intended}",
                rationale=f"est ${est_cost:.4f} > headroom ${headroom:.4f}; no fallback from {current}",
            )
            raise BudgetExceeded(
                f"{feature_id}: cannot afford {intended} (est ${est_cost:.4f} > "
                f"headroom ${headroom:.4f}), no cheaper alternative"
            )
        current = cheaper
        # Re-estimate? For now, assume cascading strictly cuts cost; conservative
        est_cost = est_cost * 0.3  # rough estimate, refined later via model_runs history

    devlog.log_decision(
        "cost_gate", feature_id,
        decision=f"downgrade_{intended}_to_{current}",
        rationale=f"video_spent=${spent_video:.4f}, day=${spent_day:.4f}, "
                  f"month=${spent_month:.4f}, headroom=${headroom:.4f}",
    )
    return current


def _is_local(model: str) -> bool:
    return model in {
        "executor", "reviewer", "planner",
        "researcher-text", "researcher-vision",
        "ltx-video", "ltx-video-q4",
        "flux.1-dev", "flux.1-schnell",
        "wan-2.1-14b",
        "f5-tts", "stable-audio-open",
    }


def remaining_budget(feature_id: str) -> dict:
    """Inspect current spend vs caps. Useful for dashboard."""
    sv = _spent_per_video(feature_id)
    sd = _spent_today()
    sm = _spent_this_month()
    return {
        "video_spent_usd": round(sv, 4),
        "video_remaining_usd": round(MAX_PER_VIDEO - sv, 4),
        "day_spent_usd": round(sd, 4),
        "day_remaining_usd": round(MAX_PER_DAY - sd, 4),
        "month_spent_usd": round(sm, 4),
        "month_remaining_usd": round(MAX_PER_MONTH - sm, 4),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(remaining_budget("SMOKE-001"), indent=2))
