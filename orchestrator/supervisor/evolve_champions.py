#!/usr/bin/env python3
"""
Champion set evolution.

Maintains the "winner library" that Tier 0 similarity check uses for
pre-render filtering and that Planner reads as positive prior.

Method (no human input):
    1. Top 20% by watch_through_pct in last 90 days  → promoted to champion
    2. Bottom 20% by watch_through_pct                → demoted to anti-pattern
    3. Champions older than 90 days that didn't re-qualify → archived

Diversity guardrail: keep top 5 per intent-category (not top 50 overall) to
avoid the library collapsing onto one viral style.

Output:
    - logs/devlog.sqlite events kind='champion_evolve'
    - eval/champions/index.json — lookup for Tier 0 + Planner
"""

from __future__ import annotations
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

DEVLOG = Path("logs/devlog.sqlite")
INDEX_PATH = Path("eval/champions/index.json")
WINDOW_DAYS = 90
TOP_PCT = 0.20
BOTTOM_PCT = 0.20
MAX_PER_CATEGORY = 5
MIN_SAMPLE_FOR_SIGNAL = 10


def fetch_candidates(db: sqlite3.Connection) -> list[dict]:
    """Pull all features with outcome data in the recent window."""
    rows = db.execute(f"""
        SELECT
            o.ref_id AS feature_id,
            AVG(CAST(json_extract(o.content,'$.watch_through_pct') AS REAL)) AS wt,
            MIN(o.ts) AS first_outcome_at,
            MAX(o.ts) AS last_outcome_at,
            COUNT(*) AS n_outcomes
        FROM events o
        WHERE o.kind='outcome'
          AND o.ts > datetime('now', '-{WINDOW_DAYS} days')
          AND json_extract(o.content,'$.watch_through_pct') IS NOT NULL
        GROUP BY o.ref_id
    """).fetchall()
    return [
        {
            "feature_id": fid,
            "watch_through_pct": float(wt),
            "first_outcome_at": first,
            "last_outcome_at": last,
            "n_outcomes": int(n),
        }
        for fid, wt, first, last, n in rows
    ]


def category_of(db: sqlite3.Connection, feature_id: str) -> str:
    """Best-effort: infer category from shotlist intent string."""
    row = db.execute("""
        SELECT content FROM events
        WHERE kind='model_run' AND ref_id=? AND actor='planner'
        ORDER BY ts ASC LIMIT 1
    """, (feature_id,)).fetchone()
    if not row:
        return "uncategorised"
    try:
        text = json.loads(row[0]).get("prompt_hash", "")
    except Exception:
        text = ""
    # Lightweight heuristic — first 3 chars of prompt hash as a stand-in
    # bucket so champions spread across whatever the orchestrator picks.
    # Replace with a proper LLM-tagging step when there's volume.
    return f"bucket_{text[:2]}" if text else "uncategorised"


def classify(candidates: list[dict]) -> dict:
    if len(candidates) < MIN_SAMPLE_FOR_SIGNAL:
        return {"champions": [], "anti_patterns": [],
                "reason": f"insufficient samples ({len(candidates)} < "
                          f"{MIN_SAMPLE_FOR_SIGNAL})"}

    sorted_c = sorted(candidates, key=lambda x: -x["watch_through_pct"])
    n_top = max(1, int(len(sorted_c) * TOP_PCT))
    n_bot = max(1, int(len(sorted_c) * BOTTOM_PCT))
    return {
        "champions": sorted_c[:n_top],
        "anti_patterns": sorted_c[-n_bot:],
    }


def cap_per_category(db: sqlite3.Connection, items: list[dict],
                     max_per_cat: int) -> list[dict]:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        cat = category_of(db, it["feature_id"])
        it["category"] = cat
        by_cat[cat].append(it)
    out = []
    for cat, lst in by_cat.items():
        out.extend(lst[:max_per_cat])
    return out


def main():
    if not DEVLOG.exists():
        print(f"DEVLOG missing: {DEVLOG}")
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        candidates = fetch_candidates(db)
        classified = classify(candidates)

        champions = cap_per_category(db, classified.get("champions", []),
                                      MAX_PER_CATEGORY)
        anti_patterns = classified.get("anti_patterns", [])

    output = {
        "window_days": WINDOW_DAYS,
        "evolved_at": datetime.utcnow().isoformat() + "Z",
        "champions_n": len(champions),
        "anti_patterns_n": len(anti_patterns),
        "candidates_n": len(candidates),
        "champions": champions,
        "anti_patterns": anti_patterns,
        "note": classified.get("reason"),
    }

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    print(f"Champions index -> {INDEX_PATH}")
    print(f"  candidates: {len(candidates)}")
    print(f"  champions:  {len(champions)}")
    print(f"  anti:       {len(anti_patterns)}")
    if output.get("note"):
        print(f"  note:       {output['note']}")
    for c in champions[:5]:
        print(f"    [+] {c['feature_id']:20s}  wt={c['watch_through_pct']:.3f}  "
              f"cat={c.get('category', '?')}")

    devlog.append(
        kind="champion_evolve",
        actor="supervisor",
        ref_type="system",
        ref_id=date.today().isoformat(),
        content=output,
    )


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
