#!/usr/bin/env python3
"""
Supervisor — daily cost roll-up with forecast + alerting.

Aggregates model_run cost events into:
  - per-feature totals (this video → spent so far)
  - per-modality daily breakdown
  - per-model daily breakdown
  - monthly running budget vs cap

Forecast (linear extrapolation, no external deps):
  - forecast_monthly(db)    — projects last-7d burn rate to end of month
  - forecast_per_video(db)  — moving average of last 10 completed videos

Alerting (via webhook or devlog-only):
  - 75% MTD burn → INFO alert
  - 90% MTD burn → WARN alert
  - 100%+ MTD burn → CRITICAL alert + emergency event
  - De-duplicated: no re-alert within 24h of last sent alert
  - Webhook env vars:
    COST_ALERT_WEBHOOK_URL   — Slack/Discord/generic POST endpoint
    COST_ALERT_THRESHOLD_PCT — custom first threshold (default 75)

Output: eval/reports/cost_YYYY-MM-DD.md + devlog events.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
REPORTS = Path("eval/reports")
MAX_PER_MONTH = float(os.environ.get("MAX_COST_PER_MONTH_USD", "500.00"))

# Alerting configuration.
WEBHOOK_URL: str = os.environ.get("COST_ALERT_WEBHOOK_URL", "").strip()
ALERT_THRESHOLD_PCT: float = float(os.environ.get("COST_ALERT_THRESHOLD_PCT", "75"))

# Alert levels: (threshold_pct, level_name)
ALERT_LEVELS = [
    (100.0, "CRITICAL"),
    (90.0,  "WARN"),
    (75.0,  "INFO"),
]


# ─── Existing aggregation queries ─────────────────────────────────────────────

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


# ─── Forecast functions ────────────────────────────────────────────────────────

def forecast_monthly(db: sqlite3.Connection) -> dict:
    """
    Project current burn rate (last 7 days) to end of current month.

    Uses simple linear extrapolation:
        projection = mtd_spent + days_remaining * avg_daily_rate

    Returns:
        monthly_projection_usd  — projected total spend at month-end
        daily_rate_usd          — average daily spend over the last 7 days
        days_remaining          — calendar days left in the month
        days_sampled            — actual number of days with events in the window
        mtd_spent_usd           — month-to-date actual spend
    """
    # Daily totals over the last 7 days.
    rows = db.execute("""
        SELECT DATE(ts) AS day,
               SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS day_usd
        FROM events
        WHERE kind='model_run'
          AND ts > datetime('now', '-7 days')
        GROUP BY day
        ORDER BY day
    """).fetchall()

    day_totals = [float(r[1] or 0) for r in rows]
    days_sampled = len(day_totals)
    daily_rate = sum(day_totals) / max(days_sampled, 1)

    # Month-to-date actual spend.
    mtd_row = db.execute("""
        SELECT COALESCE(SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)), 0)
        FROM events
        WHERE kind='model_run'
          AND strftime('%Y-%m', ts) = strftime('%Y-%m','now')
    """).fetchone()
    mtd_spent = float(mtd_row[0] if mtd_row else 0)

    # Days remaining in the month.
    today = date.today()
    if today.month == 12:
        days_in_month = 31
    else:
        days_in_month = (
            date(today.year, today.month + 1, 1) - date(today.year, today.month, 1)
        ).days
    days_remaining = days_in_month - today.day

    monthly_projection = mtd_spent + days_remaining * daily_rate

    return {
        "monthly_projection_usd": round(monthly_projection, 4),
        "daily_rate_usd": round(daily_rate, 4),
        "days_remaining": days_remaining,
        "days_sampled": days_sampled,
        "mtd_spent_usd": round(mtd_spent, 4),
    }


def forecast_per_video(db: sqlite3.Connection) -> dict:
    """
    Compute a per-video cost moving average over the last 10 completed videos.

    Returns:
        per_video_avg_usd   — moving average across last 10 videos
        n_videos            — how many videos contributed (may be < 10)
        min_usd / max_usd   — cost range for context
    """
    rows = db.execute("""
        SELECT ref_id,
               SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS total_usd
        FROM events
        WHERE kind='model_run' AND ref_id != ''
        GROUP BY ref_id
        ORDER BY MAX(ts) DESC
        LIMIT 10
    """).fetchall()

    costs = [float(r[1] or 0) for r in rows]
    n = len(costs)

    return {
        "per_video_avg_usd": round(sum(costs) / n, 4) if n else 0.0,
        "n_videos": n,
        "min_usd": round(min(costs), 4) if costs else 0.0,
        "max_usd": round(max(costs), 4) if costs else 0.0,
    }


def eta_to_cap(mtd_spent: float, daily_rate: float) -> float | None:
    """
    Compute days until the monthly cap is hit at the current daily rate.

    Returns None when daily_rate is zero or budget is not at risk.
    """
    remaining = MAX_PER_MONTH - mtd_spent
    if daily_rate <= 0 or remaining <= 0:
        return None
    return round(remaining / daily_rate, 1)


# ─── Alerting ──────────────────────────────────────────────────────────────────

def _last_alert_ts(db: sqlite3.Connection) -> datetime | None:
    """
    Return the timestamp of the most recent cost_alert_sent event, or None.

    Used for 24h de-duplication.
    """
    row = db.execute("""
        SELECT MAX(ts)
        FROM events
        WHERE kind = 'cost_alert_sent'
    """).fetchone()
    if row and row[0]:
        try:
            # SQLite ts format: 'YYYY-MM-DD HH:MM:SS' or ISO8601.
            raw = row[0].replace(" ", "T")
            if not raw.endswith("Z"):
                raw += "+00:00"
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _send_webhook(payload_text: str) -> bool:
    """
    POST a JSON payload to WEBHOOK_URL.

    Returns True on success, False on failure.
    The payload format is compatible with Slack, Discord, and generic webhooks.
    """
    if not WEBHOOK_URL:
        return False
    payload = json.dumps({"text": payload_text}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 400
    except urllib.error.URLError as exc:
        print(f"[cost_rollup] Webhook POST failed: {exc}", file=sys.stderr)
        return False


def maybe_send_alert(
    db: sqlite3.Connection,
    mtd: dict,
    forecast: dict,
) -> None:
    """
    Send a cost alert if the burn threshold is crossed and 24h has elapsed
    since the last alert.

    Three alert levels:
      - 75%+   → INFO
      - 90%+   → WARN
      - 100%+  → CRITICAL (also logs an emergency event)

    De-duplication: if an alert was sent within the last 24h, skip.
    """
    burn_pct = mtd["burn_rate_pct"]

    # Determine the active alert level (highest threshold crossed).
    active_level: str | None = None
    for threshold, level in ALERT_LEVELS:
        if burn_pct >= threshold:
            active_level = level
            break  # ALERT_LEVELS is sorted descending

    if active_level is None:
        return  # Below all thresholds.

    # 24h de-duplication check.
    last_ts = _last_alert_ts(db)
    now_utc = datetime.now(timezone.utc)
    if last_ts is not None:
        elapsed_h = (now_utc - last_ts).total_seconds() / 3600
        if elapsed_h < 24:
            print(
                f"[cost_rollup] Alert suppressed (last sent {elapsed_h:.1f}h ago, "
                f"de-dup window = 24h).",
                file=sys.stderr,
            )
            return

    # Build alert message.
    spent = mtd["spent_usd"]
    cap = mtd["cap_usd"]
    proj = forecast["monthly_projection_usd"]
    daily = forecast["daily_rate_usd"]
    eta = eta_to_cap(spent, daily)

    eta_str = f", ETA to cap: {eta:.0f} days" if eta is not None else ""
    msg = (
        f"[{active_level}] Budget alert: ${spent:.2f} / ${cap:.2f} "
        f"({burn_pct:.1f}%) — current rate ${daily:.2f}/day projects "
        f"${proj:.2f} by month-end{eta_str}"
    )

    print(f"[cost_rollup] {msg}", file=sys.stderr)

    # Log the alert event (always, even without a webhook).
    devlog.append(
        kind="cost_alert_sent",
        actor="supervisor",
        ref_type="system",
        ref_id=date.today().isoformat(),
        content={
            "level": active_level,
            "burn_pct": round(burn_pct, 2),
            "spent_usd": round(spent, 4),
            "cap_usd": cap,
            "monthly_projection_usd": round(proj, 4),
            "daily_rate_usd": round(daily, 4),
            "eta_days_to_cap": eta,
            "message": msg,
        },
    )

    # Log an emergency event for CRITICAL alerts.
    if active_level == "CRITICAL":
        devlog.append(
            kind="emergency",
            actor="supervisor",
            ref_type="system",
            ref_id=date.today().isoformat(),
            content={
                "type": "cost_over_budget",
                "spent_usd": round(spent, 4),
                "cap_usd": cap,
                "burn_pct": round(burn_pct, 2),
                "message": msg,
            },
        )

    # Attempt webhook delivery.
    if WEBHOOK_URL:
        sent = _send_webhook(msg)
        if not sent:
            print(
                "[cost_rollup] Webhook delivery failed; alert recorded in devlog only.",
                file=sys.stderr,
            )
    else:
        print(
            "[cost_rollup] No COST_ALERT_WEBHOOK_URL set; alert logged to devlog only.",
            file=sys.stderr,
        )


# ─── Report writer ─────────────────────────────────────────────────────────────

def write_report(metrics: dict) -> Path:
    """Write the daily cost roll-up Markdown report and return its path."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    report_path = REPORTS / f"cost_{today}.md"

    mtd = metrics["month_to_date"]
    fc_monthly = metrics["forecast_monthly"]
    fc_video = metrics["forecast_per_video"]
    cap = MAX_PER_MONTH
    eta = eta_to_cap(mtd["spent_usd"], fc_monthly["daily_rate_usd"])

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
        "## Forecast",
        "",
        f"- Monthly projection: ${fc_monthly['monthly_projection_usd']:.2f} "
        f"(at ${fc_monthly['daily_rate_usd']:.2f}/day current rate, "
        f"based on {fc_monthly['days_sampled']} days of data)",
        f"- Per-video moving avg (last 10): ${fc_video['per_video_avg_usd']:.4f} "
        f"({fc_video['n_videos']} videos; range ${fc_video['min_usd']:.4f}–${fc_video['max_usd']:.4f})",
    ]

    if eta is not None:
        parts.append(
            f"- ETA to cap: {eta:.0f} days at current rate"
        )
    else:
        parts.append("- ETA to cap: N/A (daily rate = 0 or budget not at risk)")

    parts += [
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


# ─── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not DEVLOG.exists():
        print(f"DEVLOG not found: {DEVLOG} — run from project root", file=sys.stderr)
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        metrics = {
            "per_video": cost_per_video(db),
            "per_modality_daily": cost_per_modality_daily(db),
            "per_model": cost_per_model(db),
            "month_to_date": month_to_date(db),
            "forecast_monthly": forecast_monthly(db),
            "forecast_per_video": forecast_per_video(db),
        }

        # Alert check (needs open db for de-dup query).
        maybe_send_alert(db, metrics["month_to_date"], metrics["forecast_monthly"])

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
            "monthly_projection_usd": metrics["forecast_monthly"]["monthly_projection_usd"],
            "per_video_avg_usd": metrics["forecast_per_video"]["per_video_avg_usd"],
        },
    )


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
