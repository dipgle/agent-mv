#!/usr/bin/env python3
"""
Regression detection — baseline snapshot + drift comparison.

Subcommands:
  snapshot    Create golden baseline from last 30 days (or refresh existing)
  check       Compare last 7 days vs baseline, flag >5% regressions (default)
  list-baselines  List all existing baseline snapshots

Usage:
  python regression_check.py snapshot [--force]
  python regression_check.py check
  python regression_check.py list-baselines

Output:
  - baseline.json: mean/stddev per (model, dimension)
  - regression_<date>.md: findings report
  - kind='regression_detected' events per finding
"""

from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
BASELINE_DIR = Path("eval/golden_regression")
REPORTS = Path("eval/reports")

DAYS_BASELINE = 30  # snapshot window
DAYS_CHECK = 7     # drift detection window
REGRESSION_THRESHOLD_PCT = 5.0  # >5% drop = regression


def list_baselines() -> list[dict]:
    """List all baseline snapshots in eval/golden_regression/."""
    if not BASELINE_DIR.exists():
        return []

    baselines = []
    for snapshot_file in sorted(BASELINE_DIR.glob("*.json")):
        if snapshot_file.name == "baseline.json":
            continue  # Skip the symlink/current baseline
        try:
            data = json.loads(snapshot_file.read_text())
            snapshot_at = data.get("snapshot_at")
            baselines.append({
                "file": snapshot_file.name,
                "snapshot_at": snapshot_at,
                "keys": len(data.get("baseline", {})),
            })
        except Exception:
            pass
    return baselines


def snapshot(force: bool = False) -> Path:
    """
    Create a golden baseline snapshot from devlog.

    Queries eval_tier2 and eval_tier3 events from the last DAYS_BASELINE days,
    groups by (evaluator, dimension), computes mean/stddev/count.
    Refuses to overwrite existing baseline unless --force.
    """
    if not DEVLOG.exists():
        raise FileNotFoundError(f"Devlog not found: {DEVLOG}")

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    # Check for recent baseline
    baseline_path = BASELINE_DIR / "baseline.json"
    if baseline_path.exists() and not force:
        existing = json.loads(baseline_path.read_text())
        snapshot_at = existing.get("snapshot_at")
        if snapshot_at:
            snapshot_date = date.fromisoformat(snapshot_at)
            age_days = (date.today() - snapshot_date).days
            if age_days < 7:
                print(f"⚠ Baseline already exists ({age_days}d old). Use --force to overwrite.")
                return baseline_path

    # Query devlog for tier2/tier3 eval events.
    with sqlite3.connect(DEVLOG) as db:
        rows = db.execute(f"""
            SELECT
                actor                                     AS evaluator,
                json_extract(content, '$.dimension')      AS dimension,
                CAST(json_extract(content, '$.score') AS REAL) AS score
            FROM events
            WHERE kind IN ('eval_tier2', 'eval_tier3')
              AND ts > datetime('now', ?)
              AND json_extract(content, '$.score') IS NOT NULL
        """, (f"-{DAYS_BASELINE} days",)).fetchall()

    # Group by (evaluator, dimension).
    grouped: dict[str, list[float]] = {}
    for evaluator, dimension, score in rows:
        if evaluator is None or dimension is None:
            continue
        key = f"{evaluator}::{dimension}"
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(score)

    # Compute stats.
    baseline_data: dict[str, dict[str, Any]] = {}
    for key, scores in grouped.items():
        if len(scores) < 2:
            continue  # need at least 2 samples for stddev
        mean = sum(scores) / len(scores)
        # Sample stddev (n-1).
        variance = sum((x - mean) ** 2 for x in scores) / (len(scores) - 1)
        stddev = variance ** 0.5
        baseline_data[key] = {
            "mean": round(mean, 4),
            "stddev": round(stddev, 4),
            "n": len(scores),
        }

    snapshot_data = {
        "snapshot_at": date.today().isoformat(),
        "baseline": baseline_data,
    }

    # Write baseline (overwrite any existing).
    baseline_path.write_text(json.dumps(snapshot_data, indent=2))

    # Also archive with timestamp.
    archive_name = f"baseline_{date.today().isoformat()}.json"
    (BASELINE_DIR / archive_name).write_text(json.dumps(snapshot_data, indent=2))

    print(f"Baseline snapshot created: {baseline_path}")
    print(f"  Keys: {len(baseline_data)}")
    print(f"  Archived: {BASELINE_DIR / archive_name}")

    return baseline_path


def check() -> tuple[int, Path]:
    """
    Compare last DAYS_CHECK days vs baseline.
    Flags dimensions where mean dropped >REGRESSION_THRESHOLD_PCT.

    Returns (exit_code, report_path):
      - exit_code=0: no regression
      - exit_code=2: regression detected
    """
    if not DEVLOG.exists():
        print(f"Devlog not found: {DEVLOG}")
        return 1, Path("eval/reports/regression_error.md")

    baseline_path = BASELINE_DIR / "baseline.json"
    if not baseline_path.exists():
        msg = "No baseline snapshot found. Run `python regression_check.py snapshot` first."
        print(f"⚠ {msg}")
        return 1, Path("eval/reports/regression_error.md")

    baseline = json.loads(baseline_path.read_text())
    baseline_data = baseline.get("baseline", {})

    # Query recent eval events.
    with sqlite3.connect(DEVLOG) as db:
        rows = db.execute(f"""
            SELECT
                actor                                     AS evaluator,
                json_extract(content, '$.dimension')      AS dimension,
                CAST(json_extract(content, '$.score') AS REAL) AS score
            FROM events
            WHERE kind IN ('eval_tier2', 'eval_tier3')
              AND ts > datetime('now', ?)
              AND json_extract(content, '$.score') IS NOT NULL
        """, (f"-{DAYS_CHECK} days",)).fetchall()

    # Group by (evaluator, dimension).
    grouped: dict[str, list[float]] = {}
    for evaluator, dimension, score in rows:
        if evaluator is None or dimension is None:
            continue
        key = f"{evaluator}::{dimension}"
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(score)

    # Detect regressions.
    regressions = []
    for key, scores in grouped.items():
        if len(scores) < 3:
            continue  # require at least 3 recent samples
        if key not in baseline_data:
            continue  # new dimension, not a regression

        base_info = baseline_data[key]
        base_mean = base_info["mean"]
        recent_mean = sum(scores) / len(scores)

        if base_mean == 0:
            drop_pct = 0.0
        else:
            drop_pct = (base_mean - recent_mean) / base_mean * 100

        if drop_pct > REGRESSION_THRESHOLD_PCT:
            regressions.append({
                "key": key,
                "baseline_mean": base_mean,
                "recent_mean": round(recent_mean, 4),
                "drop_pct": round(drop_pct, 2),
                "recent_n": len(scores),
            })

            # Log event for each regression finding.
            devlog.append(
                kind="regression_detected",
                actor="supervisor",
                ref_type="system",
                ref_id=key,
                content={
                    "key": key,
                    "baseline_mean": base_mean,
                    "recent_mean": recent_mean,
                    "drop_pct": drop_pct,
                    "sample_n": len(scores),
                    "checked_at": date.today().isoformat(),
                },
            )

    # Write report.
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / f"regression_{date.today().isoformat()}.md"

    parts = [
        f"# Regression Check — {date.today().isoformat()}",
        f"",
        f"Baseline: {baseline['snapshot_at']}",
        f"Check window: last {DAYS_CHECK} days",
        f"Threshold: >{REGRESSION_THRESHOLD_PCT}% drop = regression",
        f"",
    ]

    if regressions:
        parts.append(f"## Regressions detected: {len(regressions)}\n")
        parts.append("| Key | Baseline | Recent | Drop % | N |")
        parts.append("|---|---|---|---|---|")
        for r in regressions:
            parts.append(
                f"| `{r['key']}` | {r['baseline_mean']} | {r['recent_mean']} | "
                f"{r['drop_pct']}% | {r['recent_n']} |"
            )
    else:
        parts.append("✓ No regressions detected.")

    parts.append("")
    report_path.write_text("\n".join(parts))

    print(f"Regression report: {report_path}")
    print(f"  Findings: {len(regressions)}")

    exit_code = 2 if regressions else 0
    return exit_code, report_path


def main():
    parser = argparse.ArgumentParser(
        description="Regression detection: baseline snapshot + drift check"
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    # snapshot subcommand
    snap_parser = subparsers.add_parser("snapshot", help="Create baseline snapshot")
    snap_parser.add_argument("--force", action="store_true",
                            help="Overwrite existing baseline even if <7 days old")

    # check subcommand
    subparsers.add_parser("check", help="Check for regressions vs baseline")

    # list-baselines subcommand
    subparsers.add_parser("list-baselines", help="List all baseline snapshots")

    args = parser.parse_args()

    try:
        if args.command == "snapshot":
            snapshot(force=args.force)
            sys.exit(0)
        elif args.command == "check":
            exit_code, _ = check()
            sys.exit(exit_code)
        elif args.command == "list-baselines":
            baselines = list_baselines()
            if baselines:
                print(f"Baseline snapshots in {BASELINE_DIR}:")
                for b in baselines:
                    print(f"  {b['file']:40} {b['snapshot_at']} (keys={b['keys']})")
            else:
                print(f"No baselines found in {BASELINE_DIR}")
            sys.exit(0)
        else:
            # Default: check
            exit_code, _ = check()
            sys.exit(exit_code)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
