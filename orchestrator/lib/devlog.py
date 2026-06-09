"""
Devlog helper — append events to logs/devlog.sqlite.

Schema lives in eval/schema.sql (VIEWs) + base tables seeded by init-project's
startup.sh. This module only writes `events` rows; never alter schema here.
"""

from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

DEVLOG_PATH = Path("logs/devlog.sqlite")


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def append(kind: str, actor: str, ref_type: str, ref_id: str, content: dict) -> int:
    """Insert event row. Returns event id."""
    with sqlite3.connect(DEVLOG_PATH) as db:
        cur = db.execute(
            "INSERT INTO events (ts, kind, actor, ref_type, ref_id, content) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
            (kind, actor, ref_type, ref_id, json.dumps(content)),
        )
        return cur.lastrowid


def log_model_run(
    role: str,
    model: str,
    prompt: str,
    output_ref: str,
    latency_ms: int,
    accepted: int = 1,
    cost: dict | float | None = None,
    modality: str = "text",
    channel: str = "api",
    metrics: dict | None = None,
    feature_id: str = "",
    shot_idx: int | None = None,
) -> int:
    """
    Log model invocation. Used by orchestrator wrapper.

    `cost` may be either:
      - A dict from lib.cost.CostBreakdown.as_dict() (preferred — full breakdown)
      - A float (legacy, treated as total_usd)
      - None (defaults to 0)
    """
    if isinstance(cost, (int, float)):
        cost_dict = {"total_usd": float(cost), "cloud_usd": 0.0,
                     "compute_usd": 0.0, "electricity_usd": 0.0}
    elif cost is None:
        cost_dict = {"total_usd": 0.0}
    else:
        cost_dict = cost

    return append(
        kind="model_run",
        actor=role,
        ref_type="feature",
        ref_id=feature_id,
        content={
            "model": model,
            "modality": modality,
            "channel": channel,
            "tier": tier_of(model),
            "latency_ms": latency_ms,
            "cost": cost_dict,
            "cost_usd": cost_dict.get("total_usd", 0.0),  # legacy compat
            "accepted": accepted,
            "shot_idx": shot_idx,
            "output_ref": output_ref,
            "metrics": metrics or {},
            "prompt_hash": _hash(prompt),
        },
    )


def log_asset(
    feature_id: str,
    asset_type: str,
    path: str,
    shot_idx: int | None = None,
    duration_s: float = 0.0,
    size_bytes: int = 0,
    quality: dict | None = None,
) -> int:
    """Log produced artifact (keyframe / clip / voice / music / caption / final)."""
    return append(
        kind="artifact",
        actor="executor",
        ref_type="feature",
        ref_id=feature_id,
        content={
            "asset_type": asset_type,
            "path": path,
            "shot_idx": shot_idx,
            "duration_s": duration_s,
            "size_bytes": size_bytes,
            "quality": quality or {},
        },
    )


def log_decision(actor: str, feature_id: str, decision: str, rationale: str = ""):
    return append(
        kind="decision",
        actor=actor,
        ref_type="feature",
        ref_id=feature_id,
        content={"decision": decision, "rationale": rationale},
    )


def log_source(actor: str, url: str, takeaway: str, feature_id: str = ""):
    """Log external source consultation — required by Question Discipline rule."""
    return append(
        kind="source",
        actor=actor,
        ref_type="feature",
        ref_id=feature_id,
        content={"url": url, "takeaway": takeaway},
    )


def tier_of(model: str) -> str:
    """Classify model into tier label for cost/quality bucketing."""
    if "ollama/" in model: return "B"
    if "comfy/" in model: return "B"
    if any(x in model for x in ("groq/", "cerebras/", ":free")): return "A-"
    if "claude-sonnet" in model: return "A"
    if "claude-opus" in model or "gpt-5" in model: return "S"
    if "runway/" in model or "pika/" in model: return "S"
    return "?"


# ─── Supervisor event helpers ─────────────────────────────────────────────

def log_proposal(proposal: dict) -> int:
    """Log improvement proposal from Supervisor.

    Schema: {id, category, priority, title, hypothesis, evidence, impact,
             implementation_steps, risk, test_plan, rollback, auto_promotable, deadline}
    """
    return append(
        kind="proposal",
        actor="supervisor",
        ref_type="system",
        ref_id=proposal.get("id", ""),
        content=proposal,
    )


def log_proposal_decision(proposal_id: str, decision: str, reason: str = "",
                          actor: str = "supervisor") -> int:
    """decision in {auto_promoted, promoted, rejected, deferred, archived}"""
    return append(
        kind="proposal_decision",
        actor=actor,
        ref_type="proposal",
        ref_id=proposal_id,
        content={"decision": decision, "reason": reason},
    )


def log_canary(proposal_id: str, traffic_pct: int, days: int,
               metrics: dict, verdict: str) -> int:
    """Log canary trial result. verdict in {promote, rollback, extend}"""
    return append(
        kind="canary",
        actor="supervisor",
        ref_type="proposal",
        ref_id=proposal_id,
        content={
            "traffic_pct": traffic_pct,
            "days": days,
            "metrics": metrics,
            "verdict": verdict,
        },
    )


def log_outcome(feature_id: str, platform: str, data: dict) -> int:
    """Log post-publish outcome (watch-through, engagement)."""
    return append(
        kind="outcome",
        actor=f"platform:{platform}",
        ref_type="feature",
        ref_id=feature_id,
        content=data,
    )


def log_eval(tier: str, dimension: str, feature_id: str,
             evaluator: str, result: dict) -> int:
    """Log Tier 1/2/3 evaluation result.

    tier in {tier1, tier2, tier3}
    dimension in {technical, aesthetic, motion, narrative, brand, compliance, hook}
    evaluator: 'auto' for tier1, model name for tier2/3
    """
    return append(
        kind=f"eval_{tier}",
        actor=evaluator,
        ref_type="feature",
        ref_id=feature_id,
        content={"dimension": dimension, **result},
    )
