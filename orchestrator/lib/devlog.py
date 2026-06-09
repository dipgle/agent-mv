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
    cost_usd: float = 0.0,
    modality: str = "text",
    channel: str = "api",
    metrics: dict | None = None,
    feature_id: str = "",
    shot_idx: int | None = None,
) -> int:
    """Log model invocation. Used by orchestrator wrapper."""
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
            "cost_usd": cost_usd,
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
