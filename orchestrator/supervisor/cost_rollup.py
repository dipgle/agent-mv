#!/usr/bin/env python3
"""
Supervisor — daily cost roll-up.

Aggregates model_run cost events into:
  - per-feature totals (this video → spent so far)
  - per-modality daily breakdown
  - per-model daily breakdown
  - monthly running budget vs cap

Output: eval/reports/cost_YYYY-MM-DD.md + devlog events.
"""

from __future__ import annotations
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
REPORTS = Path("eval/reports")
MAX_PER_MONTH = float(os.environ.get("MAX_COST_PER_MONTH_USD", "500.00"))


def cost_per_video(db: sqlite3.Connection, days: int = 30) -> list[dict]:
    rows = db.execute(f"""
        SELECT
            ref_id                                                    AS feature_id,
            SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL))      AS total_usd,
            SUM(CAST(json_extract(content,'$.cost.cloud_usd') AS REAL))      AS cloud_usd,
            SUM(CAST(json_extract(content,'$.cost.compute_usd') AS REAL))    AS compute_usd,
            SUM(CAST(json_extract(content,'$.cost.electricity_usd') AS REAL))AS elec_usd,
            SUM(CAST(json_extract(content,'$.latency_ms') AS REAL))/1000.0   AS total_s,
            COUNT(*)                                                  AS n_calls
        FROM events
        WHERE kind='model_run' AND ref_id != ''
          AND ts > datetime('now', '-{days} days')
        GROUP BY ref_id
        ORDER BY total_usd DESC
        LIMIT 50
    """).fetchall()
    cols = ["feature_id", "total_usd", "cloud_usd", "compute_usd",
            "elec_usd", "total_s", "n_calls"]
    return [dict(zip(cols, r)) for r in rows]


def cost_per_modality_daily(db: sqlite3.Connection, days: int = 30) -> list[dict]:
    rows = db.execute(f"""
        SELECT
            DATE(ts)                                                  AS day,
            json_extract(content,'$.modality')                        AS modality,
            COUNT(*)                                                  AS n,
            SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL))      AS total_usd
        FROM events
        WHERE kind='model_run'
          AND ts > datetime('now', '-{days} days')
        GROUP BY day, modality
        ORDER BY day DESC, total_usd DESC
    """).fetchall()
    cols = ["day", "modality", "n", "total_usd"]
    return [dict(zip(cols, r)) for r in rows]


def cost_per_model(db: sqlite3.Connection, days: int = 30) -> list[dict]:
    rows = db.execute(f"""
        SELECT
            json_extract(content,'$.model')                           AS model,
            json_extract(content,'$.tier')                            AS tier,
            COUNT(*)                                                  AS n,
            SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL))      AS total_usd,
            AVG(CAST(json_extract(content,'$.cost.total_usd') AS REAL))      AS avg_usd
        FROM events
        WHERE kind='model_run'
          AND ts > datetime('now', '-{days} days')
        GROUP BY model, tier
        ORDER BY total_usd DESC
    """).fetchall()
    cols = ["model", "tier", "n", "total_usd", "avg_usd"]
    return [dict(zip(cols, r)) for r in rows]


def month_to_date(db: sqlite3.Connection) -> dict:
    row = db.execute("""
        SELECT
            COALESCE(SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)), 0) AS spent,
            COUNT(DISTINCT ref_id) AS videos,
            COUNT(*) AS calls
        FROM events
        WHERE kind='model_run'
          AND strftime('%Y-%m', ts) = strftime('%Y-%m','now')
    """).fetchone()
    return {
        "spent_usd": float(row[0]),
        "videos": int(row[1]),
        "calls": int(row[2]),
        "cap_usd": MAX_PER_MONTH,
        "remaining_usd": MAX_PER_MONTH - float(row[0]),
        "burn_rate_pct": float(row[0]) / MAX_PER_MONTH * 100 if MAX_PER_MONTH else 0,
    }


def write_report(metrics: dict) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    report_path = REPORTS / f"cost_{today}.md"

    mtd = metrics["month_to_date"]
    parts = [
        f"# Cost roll-up — {today}",
        "",
        "## Month-to-date budget",
        "",
        f"- **Spent**: ${mtd['spent_usd']:.4f}",
        f"- **Cap**:   ${mtd['cap_usd']:.2f}",
        f"- **Remaining**: ${mtd['remaining_usd']:.4f}",
        f"- **Burn**: {mtd['burn_rate_pct']:.1f}%",
        f"- Videos this month: {mtd['videos']}, calls: {mtd['calls']}",
        "",
        "## Top spend per video (last 30d)",
        "",
        "| Feature | Total $ | Cloud $ | Compute $ | Elec $ | Sec | Calls |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in metrics["per_video"][:10]:
        parts.append(
            f"| `{v['feature_id']}` | ${v['total_usd']:.4f} | "
            f"${v['cloud_usd']:.4f} | ${v['compute_usd']:.4f} | "
            f"${v['elec_usd']:.4f} | {int(v['total_s'])} | {v['n_calls']} |"
        )

    parts += ["", "## Spend per modality (this week, daily)", "",
              "| Day | Modality | N | Total $ |", "|---|---|---|---|"]
    for r in metrics["per_modality_daily"][:30]:
        parts.append(
            f"| {r['day']} | {r['modality'] or '?'} | {r['n']} | "
            f"${r['total_usd']:.4f} |"
        )

    parts += ["", "## Spend per model (last 30d)", "",
              "| Model | Tier | N | Total $ | Avg $ |", "|---|---|---|---|---|"]
    for r in metrics["per_model"][:15]:
        parts.append(
            f"| `{r['model'] or '?'}` | {r['tier'] or '?'} | {r['n']} | "
            f"${r['total_usd']:.4f} | ${r['avg_usd']:.4f} |"
        )

    parts.append("")
    report_path.write_text("\n".join(parts))
    return report_path


def main():
    if not DEVLOG.exists():
        print(f"DEVLOG not found: {DEVLOG} — run from project root")
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        metrics = {
            "per_video": cost_per_video(db),
            "per_modality_daily": cost_per_modality_daily(db),
            "per_model": cost_per_model(db),
            "month_to_date": month_to_date(db),
        }

    report_path = write_report(metrics)
    print(f"Cost report: {report_path}")

    devlog.append(
        kind="cost_summary",
        actor="supervisor",
        ref_type="system",
        ref_id=date.today().isoformat(),
        content={
            "report_path": str(report_path),
            "month_to_date_usd": metrics["month_to_date"]["spent_usd"],
            "burn_rate_pct": metrics["month_to_date"]["burn_rate_pct"],
        },
    )

    # Alert if burn rate >75%
    if metrics["month_to_date"]["burn_rate_pct"] > 75:
        devlog.log_decision(
            "supervisor", "system",
            decision="cost_alert",
            rationale=(
                f"Month-to-date burn {metrics['month_to_date']['burn_rate_pct']:.1f}% — "
                f"only ${metrics['month_to_date']['remaining_usd']:.4f} left vs cap "
                f"${metrics['month_to_date']['cap_usd']:.2f}"
            ),
        )


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
