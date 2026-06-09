#!/usr/bin/env python3
"""
Supervisor Job A — System audit (daily cron).

Detects 4 classes of issues from devlog.sqlite:
  A1. Bottleneck   — slowest model calls (p95 latency)
  A2. Regression   — golden set score drift vs baseline
  A3. Waste        — duplicated prompts, idle GPU, retry-on-success
  A4. Reliability  — timeout / error rate per model

Output: eval/reports/audit_YYYY-MM-DD.md + devlog events.
"""

from __future__ import annotations
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Allow running standalone via `python orchestrator/supervisor/audit.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
REPORTS = Path("eval/reports")
DAYS_WINDOW = 7  # rolling window for trend analysis


# ─── A1. Bottleneck ──────────────────────────────────────────────────────
def detect_bottlenecks(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(f"""
        SELECT
            actor                                                AS role,
            json_extract(content,'$.model')                      AS model,
            json_extract(content,'$.modality')                   AS modality,
            COUNT(*)                                             AS n,
            AVG(CAST(json_extract(content,'$.latency_ms') AS REAL)) AS avg_ms,
            MAX(CAST(json_extract(content,'$.latency_ms') AS REAL)) AS max_ms,
            SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL))
                                                                 AS total_usd
        FROM events
        WHERE kind='model_run'
          AND ts > datetime('now', ?)
        GROUP BY role, model, modality
        HAVING n >= 3
        ORDER BY avg_ms DESC
        LIMIT 10
    """, (f"-{DAYS_WINDOW} days",)).fetchall()
    cols = ["role", "model", "modality", "n", "avg_ms", "max_ms", "total_usd"]
    return [dict(zip(cols, r)) for r in rows]


# ─── A2. Regression (vs baseline snapshot) ───────────────────────────────
def detect_regressions(db: sqlite3.Connection) -> list[dict]:
    """
    Compare last 7 days mean tier2 scores vs baseline snapshot.
    Baseline lives in eval/golden_regression/baseline.json (manually seeded
    or auto-snapshotted by `python supervisor/regression_check.py snapshot`).
    """
    baseline_path = Path("eval/golden_regression/baseline.json")
    if not baseline_path.exists():
        return [{"note": "no baseline snapshot — run regression_check.py snapshot once"}]
    baseline = json.loads(baseline_path.read_text())

    rows = db.execute(f"""
        SELECT
            actor                                              AS evaluator,
            json_extract(content,'$.dimension')                AS dimension,
            AVG(CAST(json_extract(content,'$.score') AS REAL)) AS recent_avg,
            COUNT(*)                                           AS n
        FROM events
        WHERE kind IN ('eval_tier2','eval_tier3')
          AND ts > datetime('now', ?)
          AND json_extract(content,'$.score') IS NOT NULL
        GROUP BY evaluator, dimension
        HAVING n >= 5
    """, (f"-{DAYS_WINDOW} days",)).fetchall()

    regressions = []
    for evaluator, dimension, recent_avg, n in rows:
        key = f"{evaluator}::{dimension}"
        base = baseline.get(key)
        if base is None:
            continue
        drop_pct = (base - recent_avg) / base * 100 if base else 0
        if drop_pct > 5:  # >5% drop = regression
            regressions.append({
                "evaluator": evaluator,
                "dimension": dimension,
                "baseline": round(base, 2),
                "recent_avg": round(recent_avg, 2),
                "drop_pct": round(drop_pct, 2),
                "sample_n": n,
            })
    return regressions


# ─── A3. Waste ───────────────────────────────────────────────────────────
def detect_waste(db: sqlite3.Connection) -> list[dict]:
    """Duplicate prompt_hash on same model = candidate for caching."""
    rows = db.execute(f"""
        SELECT
            json_extract(content,'$.prompt_hash')              AS ph,
            json_extract(content,'$.model')                    AS model,
            COUNT(*)                                           AS dupes,
            SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL))
                                                               AS spent_usd
        FROM events
        WHERE kind='model_run'
          AND ts > datetime('now', ?)
          AND json_extract(content,'$.prompt_hash') != ''
        GROUP BY ph, model
        HAVING dupes >= 2
        ORDER BY spent_usd DESC
        LIMIT 10
    """, (f"-{DAYS_WINDOW} days",)).fetchall()
    cols = ["prompt_hash", "model", "dupes", "spent_usd"]
    return [dict(zip(cols, r)) for r in rows]


# ─── A4. Reliability ─────────────────────────────────────────────────────
def detect_reliability(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(f"""
        SELECT
            json_extract(content,'$.model')                      AS model,
            COUNT(*)                                             AS n,
            SUM(CAST(json_extract(content,'$.accepted') AS INTEGER)) * 1.0
                / COUNT(*)                                       AS success_rate,
            AVG(CAST(json_extract(content,'$.latency_ms') AS REAL)) AS avg_ms
        FROM events
        WHERE kind='model_run' AND ts > datetime('now', ?)
        GROUP BY model
        HAVING n >= 5
        ORDER BY success_rate ASC
        LIMIT 10
    """, (f"-{DAYS_WINDOW} days",)).fetchall()
    cols = ["model", "n", "success_rate", "avg_ms"]
    out = [dict(zip(cols, r)) for r in rows]
    # Only return rows below threshold
    return [r for r in out if (r["success_rate"] or 1.0) < 0.95]


# ─── Report writer ───────────────────────────────────────────────────────
def write_report(findings: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / f"audit_{date.today().isoformat()}.md"

    parts = [
        f"# System audit — {date.today().isoformat()}",
        f"",
        f"Window: last {DAYS_WINDOW} days. Generated by supervisor/audit.py.",
        f"",
        "## A1. Bottlenecks (slowest by avg latency)",
        "",
    ]
    if findings["bottlenecks"]:
        parts.append("| Role | Model | Modality | N | Avg ms | Max ms | Spent USD |")
        parts.append("|---|---|---|---|---|---|---|")
        for b in findings["bottlenecks"]:
            parts.append(
                f"| {b['role']} | `{b['model']}` | {b['modality']} | {b['n']} | "
                f"{int(b['avg_ms'] or 0)} | {int(b['max_ms'] or 0)} | "
                f"${(b['total_usd'] or 0):.4f} |"
            )
    else:
        parts.append("_no data_")

    parts += ["", "## A2. Regressions (≥5% drop vs baseline)", ""]
    if findings["regressions"]:
        for r in findings["regressions"]:
            if "note" in r:
                parts.append(f"⚠ {r['note']}")
            else:
                parts.append(
                    f"- **{r['evaluator']} :: {r['dimension']}** "
                    f"baseline {r['baseline']} → recent {r['recent_avg']} "
                    f"({r['drop_pct']}% drop, n={r['sample_n']})"
                )
    else:
        parts.append("_no regressions detected_")

    parts += ["", "## A3. Waste (duplicate prompts → caching opportunity)", ""]
    if findings["waste"]:
        parts.append("| Model | Dupes | Spent USD |")
        parts.append("|---|---|---|")
        total_waste = 0.0
        for w in findings["waste"]:
            spent = w["spent_usd"] or 0
            total_waste += spent
            parts.append(f"| `{w['model']}` | {w['dupes']} | ${spent:.4f} |")
        parts.append(f"")
        parts.append(f"**Total wasteful spend: ${total_waste:.4f}** (last {DAYS_WINDOW}d)")
    else:
        parts.append("_no significant duplication_")

    parts += ["", "## A4. Reliability (success_rate < 95%)", ""]
    if findings["reliability"]:
        parts.append("| Model | N | Success rate | Avg ms |")
        parts.append("|---|---|---|---|")
        for r in findings["reliability"]:
            sr = (r["success_rate"] or 0) * 100
            parts.append(
                f"| `{r['model']}` | {r['n']} | {sr:.1f}% | {int(r['avg_ms'] or 0)} |"
            )
    else:
        parts.append("_all models above 95% success_")

    parts.append("")
    report_path.write_text("\n".join(parts))
    return report_path


def main():
    if not DEVLOG.exists():
        print(f"DEVLOG not found: {DEVLOG} — run from project root")
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        findings = {
            "bottlenecks": detect_bottlenecks(db),
            "regressions": detect_regressions(db),
            "waste": detect_waste(db),
            "reliability": detect_reliability(db),
        }

    report_path = write_report(findings)
    print(f"Audit report: {report_path}")

    # Log summary event (not the full findings, that's in the file)
    devlog.append(
        kind="audit_summary",
        actor="supervisor",
        ref_type="system",
        ref_id=date.today().isoformat(),
        content={
            "report_path": str(report_path),
            "bottleneck_n": len(findings["bottlenecks"]),
            "regression_n": len(findings["regressions"]),
            "waste_n": len(findings["waste"]),
            "reliability_issue_n": len(findings["reliability"]),
        },
    )


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
