"""
Cost gate — hard budget cap per video + cascade fallback.

Used inline by pipeline.py before each model call:

    model = cost_gate.gate(feature_id, intended="executor", est_cost=0.05)
    # ^ may return a downgraded model name if budget tight

Configurable via env:
    MAX_COST_PER_VIDEO_USD     (default 5.00)
    MAX_COST_PER_DAY_USD       (default 50.00)
    MAX_COST_PER_MONTH_USD     (default 500.00)
    COMMERCIAL_MODE            (default "1" = use only Apache 2.0 / MIT / CC0 models)
                               Set to "0" to opt into personal/research models
                               (FLUX.1-dev BFL Non-Commercial, LTX-Video research-only).
                               COMMERCIAL_MODE=0 must NEVER be used for client/commercial work.

VISUAL GEN CASCADE — two chains depending on COMMERCIAL_MODE:

  COMMERCIAL_MODE=1 (default, safe for any use):
    flux.1-pro → flux.1-schnell → None
    runway-gen-3 → wan-2.1-t2v-14b → None

  COMMERCIAL_MODE=0 (personal/research only — NOT for client work):
    flux.1-pro → flux.1-dev → flux.1-schnell → None
    runway-gen-3 → wan-2.1-t2v-14b → ltx-video → ltx-video-q4 → None

  The active chain is selected at module load time from the env var.
  See docs/conventions.md "License hygiene" for the full policy.
"""

from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import devlog

DEVLOG_PATH = Path("logs/devlog.sqlite")

MAX_PER_VIDEO = float(os.environ.get("MAX_COST_PER_VIDEO_USD", "5.00"))
MAX_PER_DAY = float(os.environ.get("MAX_COST_PER_DAY_USD", "50.00"))
MAX_PER_MONTH = float(os.environ.get("MAX_COST_PER_MONTH_USD", "500.00"))

# COMMERCIAL_MODE=1 (default) enforces Apache 2.0 / MIT / CC0 models only.
# COMMERCIAL_MODE=0 opts into personal/research models (non-commercial licenses).
# See module docstring and docs/conventions.md "License hygiene".
COMMERCIAL_MODE: bool = os.environ.get("COMMERCIAL_MODE", "1") != "0"


# ─── Cascade chains ──────────────────────────────────────────────────────────
#
# Cascade philosophy:
#   Local Tier B → Tier A− legit free (Groq/Cerebras/Codestral/OpenRouter)
#     → Tier S free pool (Codex rotation, Adjudicator/Architect ONLY)
#     → Tier S paid (last resort, logged decision)
#
# Visual gen has TWO cascade chains (commercial vs personal).
# _build_cascade() selects the active one at module load time.

def _build_cascade(commercial_mode: bool) -> dict[str, str | None]:
    """
    Build the full cascade dict.

    When commercial_mode=True (default): visual cascade skips non-commercial
    models (FLUX.1-dev, LTX-Video). When False: includes them as intermediate
    steps for personal/research use.
    """
    # ── Text LLM cascade (same in both modes) ────────────────────────────
    cascade: dict[str, str | None] = {
        # Adjudicator chain: try Codex pool first (free if quota), then paid
        "adjudicator":               "adjudicator-paid",
        "adjudicator-paid":          "reviewer-paid",
        "reviewer-paid":             "reviewer-fallback",
        "reviewer-fallback":         "reviewer",
        "reviewer":                  None,   # local — cannot downgrade further

        # Architect: Codex pool → paid Opus (no further fallback;
        # architecture decisions must be high-quality)
        "architect":                 "adjudicator-paid",

        # Executor chain: prefer fast free first
        "executor-paid":             "executor-fallback-fast",
        "executor-fallback-fast":    "code-fallback",   # Codestral free
        "code-fallback":             "executor",
        "executor":                  None,

        "planner-script-hard":       "planner",
        "planner":                   None,

        "researcher-bulk":           "researcher-text",
        "researcher-text":           None,

        # ── Audio cascade ─────────────────────────────────────────────────
        # Voice TTS: paid API → local F5-TTS (Apache 2.0)
        "elevenlabs-tts":            "f5-tts",
        "f5-tts":                    None,

        # Music: paid jingle APIs → free stock (Pixabay API, royalty-free)
        # Stable Audio Open is REMOVED — CC-BY-NC non-commercial only.
        "suno-v4":                   "pixabay-music",
        "pixabay-music":             None,  # local CC0 fallback is internal to stock_music.py
    }

    # ── Visual gen cascade — COMMERCIAL_MODE=1 (default) ─────────────────
    # Only Apache 2.0 / MIT models in the chain. This is the default and the
    # ONLY safe chain for any client or commercial production work.
    if commercial_mode:
        cascade.update({
            # Image keyframe: paid API → FLUX.1-schnell (Apache 2.0, 4-step)
            "flux.1-pro":            "flux.1-schnell",
            "flux.1-schnell":        None,

            # Image-to-video: paid cloud → Wan2.1-T2V-14B (Apache 2.0)
            "runway-gen-3":          "wan-2.1-t2v-14b",
            "wan-2.1-t2v-14b":       None,
        })
    # ── Visual gen cascade — COMMERCIAL_MODE=0 (personal/research only) ──
    # Includes non-commercial models (FLUX.1-dev, LTX-Video) as intermediate
    # steps between high-quality paid APIs and the commercial-OK local models.
    # ⚠ NEVER use COMMERCIAL_MODE=0 for client deliverables.
    else:
        cascade.update({
            # Image keyframe: paid API → dev (BFL non-commercial) → schnell (Apache 2.0)
            "flux.1-pro":            "flux.1-dev",
            "flux.1-dev":            "flux.1-schnell",   # final fallback is still commercial-OK
            "flux.1-schnell":        None,

            # Image-to-video: paid cloud → Wan2.1 → LTX (research) → LTX Q4
            "runway-gen-3":          "wan-2.1-t2v-14b",
            "wan-2.1-t2v-14b":       "ltx-video",        # research-only; personal use only
            "ltx-video":             "ltx-video-q4",
            "ltx-video-q4":          None,
        })

    return cascade


# Active cascade — resolved once at import time from COMMERCIAL_MODE env var.
COST_CASCADE: dict[str, str | None] = _build_cascade(COMMERCIAL_MODE)


class BudgetExceeded(Exception):
    """Raised when no cheaper fallback exists and budget is blown."""
    pass


# ─── Web Chat Router (Tier W) ─────────────────────────────────────────────────
# web_chat/* calls are cost=0 (free anon web UI, no API key used).
# Soft quota: 10 calls/hour per provider, tracked in eval/web_chat_quota.json.
# This quota is ALSO enforced client-side in the MCP server (mcp/web-chat-router).
# The Python-side check here guards the orchestrator's budget accounting.

WEB_CHAT_QUOTA_PER_HOUR = int(os.environ.get("WCR_QUOTA_PER_HOUR", "10"))
WEB_CHAT_QUOTA_FILE = Path("eval/web_chat_quota.json")


def _load_web_chat_quota() -> dict:
    """Load per-provider quota state from JSON file. Returns empty dict on error."""
    if not WEB_CHAT_QUOTA_FILE.exists():
        return {}
    try:
        return json.loads(WEB_CHAT_QUOTA_FILE.read_text())
    except Exception:
        return {}


def _hour_window() -> str:
    """ISO timestamp for the start of the current UTC hour (minute/second zeroed)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now.isoformat()


def is_web_chat(model: str) -> bool:
    """Return True if model is a Tier W web chat route (cost = 0)."""
    return model.startswith("web_chat/")


def check_web_chat_quota(provider: str) -> bool:
    """Return True if the provider is within the soft hourly quota."""
    state = _load_web_chat_quota()
    entry = state.get(provider)
    current_window = _hour_window()
    if not entry or entry.get("windowStart") != current_window:
        return True  # new window — always allow
    return int(entry.get("count", 0)) < WEB_CHAT_QUOTA_PER_HOUR


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
    # Tier W (web chat) — cost = 0, bypass budget math entirely.
    # Soft quota is enforced separately via check_web_chat_quota().
    if is_web_chat(intended):
        return intended

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
    """Return True if the model runs fully locally with no cloud API cost."""
    return model in {
        # Text models (Ollama)
        "executor", "reviewer", "planner",
        "researcher-text", "researcher-vision",
        # Image gen — commercial-OK default
        "flux.1-schnell",
        # Image gen — personal/research only (COMMERCIAL_MODE=0 only)
        "flux.1-dev",
        # Video gen — commercial-OK default (Apache 2.0)
        "wan-2.1-t2v-14b",
        # Video gen — personal/research only (research license)
        "ltx-video", "ltx-video-q4",
        # Audio
        "f5-tts",
        # Music: pixabay-music is HTTP but free/no API cost; treat as local for
        # budget purposes since it never triggers cloud billing.
        "pixabay-music",
    }


# ─── Codex pool discipline ────────────────────────────────────────────────
# Roles allowed to use the OpenAI Codex pool (rotated free trial accounts).
# Anything else requesting `codex` is rejected to prevent burning the pool on
# high-volume Executor/Reviewer traffic.
CODEX_POOL_ALLOWED_ROLES = {"adjudicator", "architect"}


def is_codex_role(model: str) -> bool:
    return model in ("adjudicator", "architect")


def assert_codex_quota_role(role: str) -> None:
    """Raise if caller is using a non-S role with codex-routed model."""
    if role not in CODEX_POOL_ALLOWED_ROLES:
        raise BudgetExceeded(
            f"role={role} is not allowed on the Codex pool. "
            f"Allowed: {CODEX_POOL_ALLOWED_ROLES}. "
            f"Use 'executor-fallback-fast' or 'code-fallback' for high-volume."
        )


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
