#!/usr/bin/env python3
"""
Supervisor — generate improvement proposals.

Reads:
  - eval/benchmarks/external_sources_*.json (from scan.py)
  - audit + cost rollup events from devlog
  - current infra/models.md inventory

For each finding, asks an LLM (via litellm 'planner-script-hard' route) to:
  1. Compare against our stack
  2. Estimate impact (cost delta, latency delta, quality delta)
  3. Output structured proposal JSON

Persists proposals to:
  - logs/devlog.sqlite (kind='proposal')
  - eval/reports/improvement_queue.md
"""

from __future__ import annotations
import glob
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

try:
    from lib import litellm_client
    HAS_LITELLM = True
except ImportError:
    HAS_LITELLM = False

BENCHMARKS = Path("eval/benchmarks")
REPORTS = Path("eval/reports")
MODELS_MD = Path("infra/models.md")


PROPOSAL_SYSTEM = """You are the R&D Supervisor for a multi-agent video pipeline.

Given a finding (new model, paper, pricing change, custom node) and our current
stack, output ONE improvement proposal as strict JSON:

{
  "category": "cost" | "quality" | "speed" | "reliability",
  "priority": "high" | "medium" | "low",
  "title": "<concise action verb>",
  "hypothesis": "<1-2 sentence claim of measurable improvement>",
  "evidence": [{"source": "<url>", "takeaway": "<one-line>"}],
  "impact": {
    "cost_per_video_delta_usd": <float, negative = cheaper>,
    "latency_delta_pct": <int, negative = faster>,
    "vram_delta_gb": <float>,
    "quality_delta_pct": <int, negative = worse>
  },
  "implementation_steps": ["<step 1>", "..."],
  "risk": "low" | "medium" | "high",
  "test_plan": ["<step>"],
  "rollback": "<how to revert>",
  "auto_promotable": <true if cost/speed gain AND quality_delta_pct >= -5 AND risk=low>
}

If the finding is irrelevant to our stack, output: {"skip": true, "reason": "..."}
"""


def load_latest_scan() -> dict | None:
    files = sorted(glob.glob(str(BENCHMARKS / "external_sources_*.json")))
    if not files:
        return None
    return json.loads(Path(files[-1]).read_text())


def load_stack_context() -> str:
    if MODELS_MD.exists():
        return MODELS_MD.read_text()
    return "(no infra/models.md found)"


def propose_for_finding(finding: dict, stack_context: str) -> dict | None:
    """Ask LLM to propose. Returns proposal dict or None if skipped."""
    if not HAS_LITELLM:
        # Stub mode: produce a minimal proposal automatically
        return _stub_proposal(finding)

    prompt = (
        f"Our current stack:\n{stack_context}\n\n"
        f"Finding to evaluate:\n{json.dumps(finding, ensure_ascii=False)}\n\n"
        "Propose ONE improvement (or skip if not applicable)."
    )
    try:
        result, _ = litellm_client.call_json(
            role="supervisor",
            model="planner",  # use local planner to keep it cheap
            prompt=prompt,
            system=PROPOSAL_SYSTEM,
            feature_id="",
            skip_gate=True,  # supervisor itself shouldn't trigger cost_gate
        )
    except Exception as e:
        devlog.append("propose_error", "supervisor", "system", "",
                      {"finding": finding, "error": str(e)})
        return None

    if result.get("skip"):
        return None
    return result


def _stub_proposal(finding: dict) -> dict:
    """Fallback when no LLM available — minimal proposal structure."""
    source = finding.get("source", "?")
    title = finding.get("title") or finding.get("model_id") or finding.get("model", "?")
    return {
        "category": "quality",
        "priority": "low",
        "title": f"Evaluate {title}",
        "hypothesis": f"New {source} finding may improve pipeline if applicable.",
        "evidence": [{"source": finding.get("url", source),
                     "takeaway": str(finding)[:200]}],
        "impact": {
            "cost_per_video_delta_usd": 0,
            "latency_delta_pct": 0,
            "vram_delta_gb": 0,
            "quality_delta_pct": 0,
        },
        "implementation_steps": [
            "Read source URL fully",
            "Compare against our existing stack",
            "Run golden set if applicable",
        ],
        "risk": "medium",
        "test_plan": ["Manual investigation needed"],
        "rollback": "N/A — investigative only",
        "auto_promotable": False,
        "_stub": True,
    }


def write_queue(proposals: list[dict]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    queue_path = REPORTS / "improvement_queue.md"

    parts = ["# Improvement queue", "",
             f"Generated {date.today().isoformat()}. "
             f"{len(proposals)} active proposals.",
             ""]

    # Group by priority
    by_pri = {"high": [], "medium": [], "low": []}
    for p in proposals:
        by_pri.setdefault(p.get("priority", "low"), []).append(p)

    for pri in ("high", "medium", "low"):
        if not by_pri.get(pri):
            continue
        parts.append(f"## {pri.upper()} priority")
        parts.append("")
        for p in by_pri[pri]:
            parts.append(f"### `{p['id']}` — {p['title']}")
            parts.append("")
            parts.append(f"- **Category**: {p.get('category')}")
            parts.append(f"- **Risk**: {p.get('risk')} | **Auto-promotable**: "
                         f"{p.get('auto_promotable')}")
            parts.append(f"- **Hypothesis**: {p.get('hypothesis', '')}")
            impact = p.get("impact", {})
            parts.append(
                f"- **Impact**: cost Δ ${impact.get('cost_per_video_delta_usd', 0):+.4f}/video, "
                f"latency Δ {impact.get('latency_delta_pct', 0):+d}%, "
                f"quality Δ {impact.get('quality_delta_pct', 0):+d}%"
            )
            parts.append("- **Evidence**:")
            for ev in p.get("evidence", []):
                parts.append(f"  - [{ev.get('takeaway', '?')}]({ev.get('source', '#')})")
            parts.append("- **Implementation**:")
            for step in p.get("implementation_steps", []):
                parts.append(f"  - {step}")
            parts.append("")

    queue_path.write_text("\n".join(parts))
    return queue_path


def main():
    scan_data = load_latest_scan()
    if not scan_data:
        print("No scan data found; run supervisor/scan.py first")
        sys.exit(1)
    stack = load_stack_context()

    proposals = []
    deadline = (date.today() + timedelta(days=30)).isoformat()

    findings = []
    for section, items in scan_data.items():
        findings.extend(items)

    for f in findings[:30]:  # cap to control cost
        p = propose_for_finding(f, stack)
        if not p:
            continue
        p["id"] = f"PROP-{date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
        p["deadline"] = deadline
        devlog.log_proposal(p)
        proposals.append(p)

    queue_path = write_queue(proposals)
    print(f"Improvement queue: {queue_path}  ({len(proposals)} proposals)")

    devlog.append(
        kind="propose_summary",
        actor="supervisor",
        ref_type="system",
        ref_id=date.today().isoformat(),
        content={"proposal_n": len(proposals), "queue_path": str(queue_path)},
    )


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
