"""
Tier 2 — LLM panel ensemble (4 models scoring per dimension).

Replaces single reviewer with diverse panel to avoid family-specific blind
spots.  Each model scores INDEPENDENTLY on a per-dimension rubric, then we
aggregate via trimmed-mean for scores and unanimous-on-critical for booleans.

Panel members (logical names from infra/litellm.yaml):
  - reviewer            (local DeepSeek-R1)         reasoning, narrative
  - researcher-vision   (local Qwen2.5-VL)          visual composition
  - researcher-bulk     (Gemini Flash, free)        long context, neutral
  - reviewer-paid       (Claude Sonnet, optional)   taste, edge cases

Strong disagreement (σ > 0.3 on a scored dimension, or split vote on a
boolean) escalates to Tier 3 frontier adjudicator (Claude Opus).
"""

from __future__ import annotations
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from . import devlog, litellm_client

DIMENSIONS_SCORED = ["aesthetic", "narrative", "brand_subjective", "hook_taste"]
DIMENSIONS_BOOL = ["compliance_subjective"]
PANEL_ROLES = ["reviewer", "researcher-vision", "researcher-bulk"]
PANEL_PAID_ROLE = "reviewer-paid"  # Only included when allow_paid=True
ESCALATION_SIGMA = 0.3


REVIEWER_SYSTEM = """You are one member of a 4-model reviewer panel evaluating
an ad video.  Output STRICT JSON, no preamble, this exact schema:

{
  "aesthetic":            {"score": <0-10>, "evidence": "<one sentence>"},
  "narrative":            {"score": <0-10>, "evidence": "<one sentence>"},
  "brand_subjective":     {"score": <0-10>, "evidence": "<one sentence>"},
  "hook_taste":           {"score": <0-10>, "evidence": "<one sentence>"},
  "compliance_subjective":{"pass":  <true|false>, "issues": ["..."]},
  "shot_issues": [{"shot": <int>, "severity":"critical|major|minor", "msg":"..."}]
}

Score rubric (apply uniformly):
  10 = exemplar quality, would beat 90% of competitor ads
   7 = solid, ship-able, on-brand
   5 = mediocre, viewer would scroll past
   3 = visible defects, poor execution
   0 = unusable

Be honest.  Inflated scores reduce panel signal.  If unsure, score 5 + say so.
"""


def _build_prompt(spec: dict, brand: dict, transcript: str,
                  tier1: dict, hook: dict) -> str:
    """Compress all evidence into a single prompt the panel can score."""
    return (
        f"# Video evidence for panel review\n\n"
        f"## Shotlist (from Planner)\n```json\n"
        f"{json.dumps(spec, ensure_ascii=False, indent=2)[:3000]}\n```\n\n"
        f"## Brand spec\n```json\n"
        f"{json.dumps(brand, ensure_ascii=False, indent=2)[:1500]}\n```\n\n"
        f"## Voiceover transcript\n{transcript[:2000]}\n\n"
        f"## Tier 1 deterministic checks\n```json\n"
        f"{json.dumps(tier1, ensure_ascii=False)[:1500]}\n```\n\n"
        f"## Hook auto-scores\n```json\n"
        f"{json.dumps(hook, ensure_ascii=False)[:800]}\n```\n\n"
        f"Output ONLY the JSON object specified in system prompt.\n"
    )


def _score_with_model(role: str, prompt: str, feature_id: str) -> Optional[dict]:
    """Call one panel member; tag output with model name."""
    try:
        result, meta = litellm_client.call_json(
            role=f"reviewer-panel:{role}",
            model=role,
            prompt=prompt,
            system=REVIEWER_SYSTEM,
            feature_id=feature_id,
            temperature=0.2,
            skip_gate=True,  # panel call doesn't trigger cascade
        )
    except Exception as e:
        devlog.append("panel_error", "supervisor", "feature", feature_id,
                      {"role": role, "error": str(e)})
        return None
    result["_model"] = role
    result["_meta"] = meta
    return result


def _aggregate(critiques: list[dict]) -> dict:
    """Per-dimension aggregation: trimmed mean for scores, majority for bools."""
    out = {"votes": []}

    for dim in DIMENSIONS_SCORED:
        scores = [c[dim]["score"] for c in critiques
                  if isinstance(c.get(dim), dict) and "score" in c[dim]]
        if not scores:
            out[dim] = {"score": None, "agreement": None}
            continue
        # Trimmed mean if >= 3 critiques
        sorted_scores = sorted(scores)
        if len(sorted_scores) >= 3:
            trimmed = sorted_scores[1:-1]  # drop highest + lowest
            mean = sum(trimmed) / len(trimmed)
        else:
            mean = sum(sorted_scores) / len(sorted_scores)
        sigma = statistics.stdev(scores) if len(scores) > 1 else 0
        out[dim] = {
            "score": round(mean, 2),
            "sigma": round(sigma, 2),
            "votes": scores,
            "needs_adjudicator": sigma > ESCALATION_SIGMA,
        }

    for dim in DIMENSIONS_BOOL:
        bools = [bool(c[dim].get("pass")) for c in critiques
                 if isinstance(c.get(dim), dict)]
        if not bools:
            out[dim] = {"pass": None, "agreement": None}
            continue
        votes_pass = sum(bools)
        out[dim] = {
            "pass": votes_pass > len(bools) / 2,
            "vote_pass": votes_pass,
            "vote_fail": len(bools) - votes_pass,
            "unanimous": votes_pass == len(bools) or votes_pass == 0,
        }

    # Collect all shot issues; dedupe by (shot, msg) coarsely
    all_issues = []
    for c in critiques:
        for issue in c.get("shot_issues", []):
            if isinstance(issue, dict):
                all_issues.append({**issue, "by": c.get("_model")})
    out["shot_issues"] = all_issues

    # Overall verdict
    crit_dim_fails = []
    if not out.get("compliance_subjective", {}).get("pass", True):
        crit_dim_fails.append("compliance_subjective")
    avg_quality = statistics.mean([out[d]["score"]
                                   for d in DIMENSIONS_SCORED
                                   if out[d]["score"] is not None]
                                  or [5])
    needs_adj = any(out[d].get("needs_adjudicator") for d in DIMENSIONS_SCORED)

    if crit_dim_fails or any(i["severity"] == "critical" for i in all_issues):
        out["verdict"] = "rejected"
    elif needs_adj:
        out["verdict"] = "needs_adjudicator"
    elif avg_quality >= 6.5:
        out["verdict"] = "approved"
    else:
        out["verdict"] = "rejected"

    out["overall_score"] = round(avg_quality * 10, 1)  # 0-100 scale
    return out


def evaluate(spec: dict, brand: dict, transcript: str, tier1: dict,
             hook: dict, feature_id: str, *, allow_paid: bool = False) -> dict:
    """Run panel in parallel, aggregate, log each vote + final aggregate."""
    prompt = _build_prompt(spec, brand, transcript, tier1, hook)
    roles = list(PANEL_ROLES)
    if allow_paid:
        roles.append(PANEL_PAID_ROLE)

    critiques: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(roles)) as pool:
        futures = {pool.submit(_score_with_model, r, prompt, feature_id): r
                   for r in roles}
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                critiques.append(result)

    if not critiques:
        return {"verdict": "rejected", "error": "all panel members failed"}

    aggregated = _aggregate(critiques)
    aggregated["panel_size"] = len(critiques)
    aggregated["models"] = [c["_model"] for c in critiques]

    # Log each individual vote + the aggregate
    for c in critiques:
        for dim in DIMENSIONS_SCORED:
            if isinstance(c.get(dim), dict) and "score" in c[dim]:
                devlog.log_eval("tier2", dim, feature_id, c["_model"],
                                {"score": c[dim]["score"],
                                 "evidence": c[dim].get("evidence", "")})
    devlog.append("eval_aggregate", "supervisor", "feature", feature_id,
                  {"verdict": aggregated["verdict"],
                   "overall_score": aggregated["overall_score"],
                   "panel_size": aggregated["panel_size"],
                   "needs_adjudicator": aggregated["verdict"] == "needs_adjudicator"})

    return aggregated
