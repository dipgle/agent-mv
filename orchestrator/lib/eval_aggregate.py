"""
Per-dimension verdict aggregator.

Combines Tier 0 (champion similarity), Tier 1 (deterministic), Tier 2 (LLM
panel), and Tier 3 (frontier adjudicator, when needed) into a single video
verdict — but never collapses to a one-number score.

Output schema:
    {
      "verdict": "approved | rejected | needs_adjudicator",
      "dimensions": {
        "technical":  {"pass": bool, "details": {...}},
        "audio":      {"pass": bool, "lufs": float, ...},
        "hook":       {"score": float, "pass": bool, ...},
        "brand":      {"pass": bool, ...},
        "aesthetic":  {"score": float, "sigma": float, ...},
        "narrative":  {"score": float, ...},
        "compliance": {"pass": bool, ...}
      },
      "shot_issues": [...],
      "blocker_dimensions": [...],
      "tier1_critical_fails": [...],
      "tier2_overall_score": float,
      "needs_adjudicator": bool
    }
"""

from __future__ import annotations
import statistics
from typing import Any

from . import devlog


CRITICAL_GATES = {
    # Dimension → must-pass (anything else triggers reject regardless of LLM)
    "technical": True,
    "audio_lufs_deviation_max": 5.0,   # +/- LUFS
}


def aggregate(tier1: dict, hook: dict, brand: dict, tier2: dict,
              feature_id: str) -> dict:
    """Merge all sources into final verdict."""
    dims: dict[str, dict] = {}
    blockers: list[str] = []

    # ── Technical ────────────────────────────────────────────────────────
    dims["technical"] = tier1.get("technical", {})
    if not dims["technical"].get("pass", True):
        blockers.append("technical")

    # ── Audio (LUFS deviation hard ceiling) ──────────────────────────────
    audio_lufs = tier1.get("audio", {}).get("lufs", {})
    dims["audio"] = audio_lufs
    if audio_lufs.get("deviation", 0) > CRITICAL_GATES["audio_lufs_deviation_max"]:
        blockers.append("audio_lufs")

    # ── Hook ─────────────────────────────────────────────────────────────
    dims["hook"] = hook
    if not hook.get("pass", True) and hook.get("hook_score", 10) < 5.0:
        blockers.append("hook")

    # ── Brand (auto + LLM combine) ───────────────────────────────────────
    auto_brand = brand
    llm_brand = tier2.get("brand_subjective", {})
    dims["brand"] = {
        "auto_pass": auto_brand.get("pass", True),
        "llm_score": llm_brand.get("score"),
        "llm_sigma": llm_brand.get("sigma"),
        "do_not_use_hits": auto_brand.get("do_not_use", {}).get("hits", []),
        "aspect_pass": auto_brand.get("aspect", {}).get("pass", True),
    }
    if not auto_brand.get("pass", True):
        blockers.append("brand")
    elif (llm_brand.get("score") or 10) < 4:
        blockers.append("brand")

    # ── Aesthetic + Narrative (LLM panel) ────────────────────────────────
    for dim in ("aesthetic", "narrative"):
        dims[dim] = tier2.get(dim, {})
        if (dims[dim].get("score") or 10) < 4:
            blockers.append(dim)

    # ── Compliance (LLM + auto) ──────────────────────────────────────────
    dims["compliance"] = tier2.get("compliance_subjective", {})
    if not dims["compliance"].get("pass", True):
        blockers.append("compliance")

    # ── Visual safety (freeze frames, scene changes) ─────────────────────
    dims["visual"] = tier1.get("visual", {})

    # ── Verdict ──────────────────────────────────────────────────────────
    needs_adj = tier2.get("verdict") == "needs_adjudicator"
    if blockers:
        verdict = "rejected"
    elif needs_adj:
        verdict = "needs_adjudicator"
    else:
        verdict = "approved"

    result = {
        "verdict": verdict,
        "dimensions": dims,
        "shot_issues": tier2.get("shot_issues", []),
        "blocker_dimensions": blockers,
        "tier1_critical_fails": tier1.get("critical_fails", []),
        "tier2_overall_score": tier2.get("overall_score"),
        "needs_adjudicator": needs_adj,
        "panel_size": tier2.get("panel_size"),
        "panel_models": tier2.get("models", []),
    }

    devlog.append("eval_verdict", "supervisor", "feature", feature_id, result)
    return result
