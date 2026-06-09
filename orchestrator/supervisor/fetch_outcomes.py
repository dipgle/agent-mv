#!/usr/bin/env python3
"""
Cron-able outcome ingestion.

Pulls fresh engagement metrics for every video that has a `publish_record`
event but no recent `outcome` event.  Each platform is queried per its own
client in lib/outcomes.py.

Publish record format (write this when a video is posted):
    devlog.append("publish_record", "user", "feature", "VID-001",
        {"platforms": [
            {"platform": "youtube", "video_id": "abc123",
             "published_at": "2026-06-01T10:00:00Z"},
            {"platform": "tiktok", "video_id": "7456..."},
            {"platform": "manual"}   # → look for out/VID-001/outcome_manual.json
        ]})

Schedule:
    daily at 03:00 (after audit + cost rollup):
        0 3 * * * python orchestrator/supervisor/fetch_outcomes.py

CLI:
    python supervisor/fetch_outcomes.py              # daily run, fetch pending
    python supervisor/fetch_outcomes.py --feature X  # one-off backfill
    python supervisor/fetch_outcomes.py --force      # ignore freshness window
"""

from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog, outcomes  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
REFRESH_DAYS = 1   # don't re-fetch within this window unless --force


def pending_features(db: sqlite3.Connection, force: bool = False) -> list[dict]:
    """
    Find features with publish_record but no recent outcome.

    Each row returned: {feature_id, platforms: [...]}
    """
    rows = db.execute("""
        SELECT ref_id AS feature_id, MAX(content) AS publish_content
        FROM events
        WHERE kind='publish_record'
        GROUP BY ref_id
    """).fetchall()

    pending = []
    for feature_id, publish_content in rows:
        try:
            publish = json.loads(publish_content)
        except Exception:
            continue

        # Last outcome fetched per platform
        last_per_platform: dict[str, str] = {}
        for plat, ts in db.execute("""
            SELECT REPLACE(actor, 'platform:', '') AS platform, MAX(ts) AS last_ts
            FROM events
            WHERE kind='outcome' AND ref_id=?
            GROUP BY platform
        """, (feature_id,)).fetchall():
            last_per_platform[plat] = ts

        platforms_to_fetch = []
        for p in publish.get("platforms", []):
            plat = p.get("platform")
            if not plat:
                continue
            if force or plat not in last_per_platform:
                platforms_to_fetch.append(p)
                continue
            # Check freshness
            last = last_per_platform[plat]
            try:
                last_dt = datetime.fromisoformat(last.replace(" ", "T"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - last_dt).days
                if age_days >= REFRESH_DAYS:
                    platforms_to_fetch.append(p)
            except Exception:
                platforms_to_fetch.append(p)

        if platforms_to_fetch:
            pending.append({"feature_id": feature_id, "platforms": platforms_to_fetch})

    return pending


def ingest(features: list[dict]) -> dict:
    """Iterate platforms per feature, call client, log outcome."""
    summary = {"features_n": len(features), "outcomes_fetched": 0,
               "errors": 0, "skipped": 0}
    for f in features:
        for p in f["platforms"]:
            outcome = outcomes.fetch_and_log(
                platform=p["platform"],
                video_id=p.get("video_id", ""),
                feature_id=f["feature_id"],
            )
            if outcome is None:
                summary["skipped"] += 1
            else:
                summary["outcomes_fetched"] += 1
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature", help="Backfill single feature_id")
    ap.add_argument("--force", action="store_true",
                    help="Ignore freshness window")
    args = ap.parse_args()

    if not DEVLOG.exists():
        print(f"DEVLOG missing: {DEVLOG}")
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        if args.feature:
            # One-off backfill
            row = db.execute("""
                SELECT content FROM events
                WHERE kind='publish_record' AND ref_id=?
                ORDER BY ts DESC LIMIT 1
            """, (args.feature,)).fetchone()
            if not row:
                print(f"No publish_record for {args.feature}")
                sys.exit(1)
            publish = json.loads(row[0])
            features = [{"feature_id": args.feature,
                         "platforms": publish.get("platforms", [])}]
        else:
            features = pending_features(db, force=args.force)

    print(f"Pending features: {len(features)}")
    summary = ingest(features)
    print(f"Fetched: {summary['outcomes_fetched']}, "
          f"skipped: {summary['skipped']}, errors: {summary['errors']}")

    devlog.append(
        kind="outcomes_summary",
        actor="supervisor",
        ref_type="system",
        ref_id=datetime.now(timezone.utc).date().isoformat(),
        content=summary,
    )


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
