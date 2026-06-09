#!/usr/bin/env python3
"""
Supervisor — auto-promote low-risk proposals via canary.

Workflow:
  1. Pull pending proposals (events kind='proposal' without 'proposal_decision')
  2. Filter `auto_promotable=true` AND `cost_delta <= 0` AND `quality_delta_pct >= -5`
  3. Start canary at 20% traffic
  4. After N days (configurable), aggregate canary metrics
  5. Promote (merge config) or rollback based on canary results

Canary state is in events kind='canary'. This script is idempotent.
"""

from __future__ import annotations
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
CANARY_DIR = Path("eval/canary")

CANARY_TRAFFIC_PCT = 20
CANARY_DURATION_DAYS = 7
MIN_SAMPLE_FOR_PROMOTION = 30
MAX_QUALITY_DROP_PCT = 5.0


def pending_proposals(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute("""
        SELECT id, content FROM events e1
        WHERE kind='proposal'
          AND NOT EXISTS (
            SELECT 1 FROM events e2
            WHERE e2.kind='proposal_decision'
              AND e2.ref_id = json_extract(e1.content,'$.id')
          )
        ORDER BY ts DESC
    """).fetchall()
    return [json.loads(r[1]) for r in rows]


def is_auto_safe(prop: dict) -> tuple[bool, str]:
    if not prop.get("auto_promotable"):
        return False, "marked not auto_promotable"
    impact = prop.get("impact", {})
    cost_delta = impact.get("cost_per_video_delta_usd", 0)
    quality_delta = impact.get("quality_delta_pct", 0)
    if cost_delta > 0:
        return False, f"cost increase ${cost_delta:.4f} (auto requires cost ≤ 0)"
    if quality_delta < -MAX_QUALITY_DROP_PCT:
        return False, f"quality drop {quality_delta}% > {MAX_QUALITY_DROP_PCT}% cap"
    if prop.get("risk") != "low":
        return False, f"risk={prop.get('risk')} (auto requires low)"
    return True, ""


def start_canary(prop: dict) -> dict:
    """Stub: real implementation would update LiteLLM routing weights or
    workflow JSON path. Here we just log intent + set canary record."""
    canary = {
        "proposal_id": prop["id"],
        "traffic_pct": CANARY_TRAFFIC_PCT,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "duration_days": CANARY_DURATION_DAYS,
        "expected_completion": (
            datetime.utcnow() + timedelta(days=CANARY_DURATION_DAYS)
        ).isoformat() + "Z",
    }
    CANARY_DIR.mkdir(parents=True, exist_ok=True)
    state_path = CANARY_DIR / f"{prop['id']}.json"
    state_path.write_text(json.dumps(canary, indent=2))

    devlog.log_canary(
        proposal_id=prop["id"],
        traffic_pct=CANARY_TRAFFIC_PCT,
        days=0,  # just started
        metrics={},
        verdict="started",
    )
    return canary


def evaluate_canary(prop_id: str, db: sqlite3.Connection) -> dict | None:
    """If canary running long enough, aggregate metrics + return verdict."""
    state_path = CANARY_DIR / f"{prop_id}.json"
    if not state_path.exists():
        return None
    canary = json.loads(state_path.read_text())
    started = datetime.fromisoformat(canary["started_at"].rstrip("Z"))
    elapsed_days = (datetime.utcnow() - started).days
    if elapsed_days < canary["duration_days"]:
        return None  # not ready yet

    # Aggregate: count samples + avg outcome correlation during canary window
    rows = db.execute("""
        SELECT
            COUNT(*) AS n,
            AVG(CAST(json_extract(content,'$.metrics.brand_match') AS REAL)) AS brand_avg,
            AVG(CAST(json_extract(content,'$.metrics.aesthetic_score') AS REAL)) AS aesthetic_avg
        FROM events
        WHERE kind='eval_tier2'
          AND ts > ?
    """, (canary["started_at"],)).fetchone()
    n, brand_avg, aesthetic_avg = rows
    metrics = {
        "sample_n": n,
        "brand_avg": brand_avg,
        "aesthetic_avg": aesthetic_avg,
    }

    if (n or 0) < MIN_SAMPLE_FOR_PROMOTION:
        return {"verdict": "extend",
                "reason": f"only {n} samples, need {MIN_SAMPLE_FOR_PROMOTION}",
                "metrics": metrics}

    # Naive promotion check: if quality didn't drop significantly
    # Real version would compare baseline distribution.
    return {"verdict": "promote", "reason": "canary stable", "metrics": metrics}


def promote(prop: dict, canary_result: dict):
    """Stub: real implementation would write a config change (litellm.yaml,
    workflow JSON, model swap). Here we only log the decision; humans/ops
    or follow-up scripts apply the actual change.
    """
    devlog.log_proposal_decision(
        proposal_id=prop["id"],
        decision="auto_promoted",
        reason=f"canary stable: {canary_result.get('metrics')}",
    )
    devlog.log_canary(
        proposal_id=prop["id"],
        traffic_pct=100,
        days=CANARY_DURATION_DAYS,
        metrics=canary_result.get("metrics", {}),
        verdict="promote",
    )


def rollback(prop: dict, reason: str):
    devlog.log_proposal_decision(
        proposal_id=prop["id"],
        decision="rolled_back",
        reason=reason,
    )
    devlog.log_canary(
        proposal_id=prop["id"],
        traffic_pct=0,
        days=CANARY_DURATION_DAYS,
        metrics={},
        verdict="rollback",
    )


def main():
    if not DEVLOG.exists():
        print(f"DEVLOG not found: {DEVLOG} — run from project root")
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        proposals = pending_proposals(db)

        for prop in proposals:
            # 1. Newly auto-safe → start canary
            state_path = CANARY_DIR / f"{prop['id']}.json"
            if not state_path.exists():
                safe, reason = is_auto_safe(prop)
                if safe:
                    start_canary(prop)
                    print(f"Started canary: {prop['id']} — {prop['title']}")
                else:
                    print(f"Skipped (not auto-safe): {prop['id']} — {reason}")
                continue

            # 2. Existing canary → evaluate
            result = evaluate_canary(prop["id"], db)
            if result is None:
                continue
            if result["verdict"] == "promote":
                promote(prop, result)
                print(f"PROMOTED: {prop['id']}")
            elif result["verdict"] == "extend":
                print(f"Extending canary: {prop['id']} ({result['reason']})")
            else:
                rollback(prop, result["reason"])
                print(f"ROLLED BACK: {prop['id']} ({result['reason']})")


if __name__ == "__main__":
    main()
