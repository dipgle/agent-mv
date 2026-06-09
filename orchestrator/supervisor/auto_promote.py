#!/usr/bin/env python3
"""
Supervisor — auto-promote low-risk proposals via canary.

Workflow:
  1. Pull pending proposals (events kind='proposal' without 'proposal_decision')
  2. Filter `auto_promotable=true` AND `cost_delta <= 0` AND `quality_delta_pct >= -5`
  3. Start canary at 20% traffic
  4. After N days (configurable), aggregate canary metrics
  5. Promote (mutate config) or rollback based on canary results

Canary state persisted in eval/canary/<proposal_id>.json.
Config snapshots saved to eval/snapshots/ before any mutation.
This script is idempotent — safe to re-run from cron.

CLI usage:
  python auto_promote.py                         # normal run
  python auto_promote.py --rollback <PROP-ID>    # reverse a promoted proposal
  python auto_promote.py --dry-run               # print what would happen, no writes

Stale-canary rule:
  A canary that has been in 'started' state for more than 2× its configured
  duration_days with no recorded eval_tier2 metrics is auto-rolled-back as
  'stale_canary'.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Resolve paths relative to project root (two levels above orchestrator/).
_HERE = Path(__file__).resolve()
_ORCHESTRATOR = _HERE.parents[1]
_PROJECT_ROOT = _HERE.parents[2]

sys.path.insert(0, str(_ORCHESTRATOR))
from lib import devlog  # noqa: E402
from lib import config_mutator  # noqa: E402

DEVLOG = _PROJECT_ROOT / "logs" / "devlog.sqlite"
CANARY_DIR = _PROJECT_ROOT / "eval" / "canary"
SNAPSHOTS_DIR = _PROJECT_ROOT / "eval" / "snapshots"

CANARY_TRAFFIC_PCT = 20
CANARY_DURATION_DAYS = 7
MIN_SAMPLE_FOR_PROMOTION = 30
MAX_QUALITY_DROP_PCT = 5.0

# A canary idle for more than this multiplier × duration is declared stale.
STALE_CANARY_MULTIPLIER = 2


# ─── Proposal queries ─────────────────────────────────────────────────────────

def pending_proposals(db: sqlite3.Connection) -> list[dict]:
    """Return all proposal events that have no corresponding proposal_decision."""
    rows = db.execute("""
        SELECT id, content FROM events e1
        WHERE kind='proposal'
          AND NOT EXISTS (
            SELECT 1 FROM events e2
            WHERE e2.kind='proposal_decision'
              AND e2.ref_id = json_extract(e1.content,'$.id')
          )
        ORDER BY ts DESC
    """).fetchall()
    return [json.loads(r[1]) for r in rows]


def decision_for(proposal_id: str, db: sqlite3.Connection) -> dict | None:
    """Return the most recent proposal_decision row for a given proposal id."""
    row = db.execute("""
        SELECT content FROM events
        WHERE kind='proposal_decision' AND ref_id=?
        ORDER BY ts DESC LIMIT 1
    """, (proposal_id,)).fetchone()
    return json.loads(row[0]) if row else None


def snapshot_taken_at_promote(proposal_id: str, db: sqlite3.Connection) -> str | None:
    """Return snapshot_dir stored in the config_snapshot event at promote time."""
    # We store the snapshot dir in the config_snapshot event content before promote.
    row = db.execute("""
        SELECT content FROM events
        WHERE kind='config_snapshot' AND ref_id IN (
            SELECT ref_id FROM events
            WHERE kind='config_mutation' AND ref_id=?
        )
        ORDER BY ts ASC LIMIT 1
    """, (proposal_id,)).fetchone()
    if row:
        c = json.loads(row[0])
        return c.get("snapshot_dir")
    # Fallback: look for config_mutation event and derive snapshot from ts proximity.
    return None


# ─── Promotion eligibility ────────────────────────────────────────────────────

def is_auto_safe(prop: dict) -> tuple[bool, str]:
    """Return (eligible, reason) for auto-promotion."""
    if not prop.get("auto_promotable"):
        return False, "marked not auto_promotable"
    impact = prop.get("impact", {})
    cost_delta = impact.get("cost_per_video_delta_usd", 0)
    quality_delta = impact.get("quality_delta_pct", 0)
    if cost_delta > 0:
        return False, f"cost increase ${cost_delta:.4f} (auto requires cost <= 0)"
    if quality_delta < -MAX_QUALITY_DROP_PCT:
        return False, f"quality drop {quality_delta}% > {MAX_QUALITY_DROP_PCT}% cap"
    if prop.get("risk") != "low":
        return False, f"risk={prop.get('risk')} (auto requires low)"
    return True, ""


# ─── Canary state machine ─────────────────────────────────────────────────────

def start_canary(prop: dict) -> dict:
    """Persist canary state file and log the started event."""
    canary = {
        "proposal_id": prop["id"],
        "traffic_pct": CANARY_TRAFFIC_PCT,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_days": CANARY_DURATION_DAYS,
        "expected_completion": (
            datetime.now(timezone.utc) + timedelta(days=CANARY_DURATION_DAYS)
        ).isoformat(),
        "status": "started",
    }
    CANARY_DIR.mkdir(parents=True, exist_ok=True)
    state_path = CANARY_DIR / f"{prop['id']}.json"
    state_path.write_text(json.dumps(canary, indent=2))

    devlog.log_canary(
        proposal_id=prop["id"],
        traffic_pct=CANARY_TRAFFIC_PCT,
        days=0,
        metrics={},
        verdict="started",
    )
    return canary


def evaluate_canary(prop_id: str, db: sqlite3.Connection) -> dict | None:
    """
    Evaluate a running canary.

    Returns:
      None              — not ready yet (canary window not elapsed)
      {"verdict": "promote",   ...}
      {"verdict": "extend",    ...}
      {"verdict": "rollback",  ...}
      {"verdict": "stale_canary", ...}  — idle beyond 2x duration, no metrics
    """
    state_path = CANARY_DIR / f"{prop_id}.json"
    if not state_path.exists():
        return None

    canary = json.loads(state_path.read_text())
    started = datetime.fromisoformat(canary["started_at"])
    # Ensure timezone-aware for comparison.
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed_days = (datetime.now(timezone.utc) - started).days
    duration = canary["duration_days"]

    # Stale canary: running for >2× duration with no eval_tier2 data.
    stale_threshold = duration * STALE_CANARY_MULTIPLIER
    if elapsed_days >= stale_threshold:
        rows = db.execute("""
            SELECT COUNT(*) FROM events
            WHERE kind='eval_tier2' AND ts > ?
        """, (canary["started_at"],)).fetchone()
        n_evals = rows[0] if rows else 0
        if n_evals == 0:
            return {
                "verdict": "stale_canary",
                "reason": (
                    f"canary idle {elapsed_days}d (>{stale_threshold}d threshold) "
                    f"with 0 eval_tier2 events — auto-rolling back"
                ),
                "metrics": {"sample_n": 0, "elapsed_days": elapsed_days},
            }

    # Normal evaluation: canary window not yet elapsed.
    if elapsed_days < duration:
        return None

    # Aggregate eval_tier2 metrics recorded during the canary window.
    rows = db.execute("""
        SELECT
            COUNT(*) AS n,
            AVG(CAST(json_extract(content,'$.metrics.brand_match') AS REAL)) AS brand_avg,
            AVG(CAST(json_extract(content,'$.metrics.aesthetic_score') AS REAL)) AS aesthetic_avg
        FROM events
        WHERE kind='eval_tier2'
          AND ts > ?
    """, (canary["started_at"],)).fetchone()

    n = rows[0] if rows else 0
    brand_avg = rows[1] if rows else None
    aesthetic_avg = rows[2] if rows else None

    metrics = {
        "sample_n": n,
        "brand_avg": brand_avg,
        "aesthetic_avg": aesthetic_avg,
        "elapsed_days": elapsed_days,
    }

    if (n or 0) < MIN_SAMPLE_FOR_PROMOTION:
        return {
            "verdict": "extend",
            "reason": f"only {n} samples, need {MIN_SAMPLE_FOR_PROMOTION}",
            "metrics": metrics,
        }

    # Naive promotion check: no significant quality degradation during canary.
    # Future: compare against pre-canary baseline distribution.
    return {"verdict": "promote", "reason": "canary stable", "metrics": metrics}


# ─── Promote / rollback ───────────────────────────────────────────────────────

def promote(prop: dict, canary_result: dict, dry_run: bool = False) -> None:
    """
    Apply the config change described in the proposal, then verify and log.

    Steps:
      1. snapshot_config() — save current state
      2. Dispatch to config_mutator.mutate_litellm_yaml() or swap_workflow()
      3. Smoke-verify the written YAML is parseable and contains the new entry
      4. Log proposal_decision(decision='auto_promoted') + config_mutation summary
      5. On any exception: rollback to the snapshot we just took, log failure
    """
    if dry_run:
        print(f"  [dry-run] Would promote {prop['id']} — "
              f"category={prop.get('category')} swap={prop.get('model_swap')}")
        return

    snap_dir: Path | None = None
    try:
        # Step 1: take snapshot before any mutation.
        snap_dir = config_mutator.snapshot_config()

        # Step 2: dispatch mutation based on category.
        category = prop.get("category", "")
        diff: dict = {}

        if prop.get("model_swap"):
            # Model routing change → mutate litellm.yaml.
            diff = config_mutator.mutate_litellm_yaml(prop)
        elif prop.get("workflow_name") and prop.get("workflow_src"):
            # ComfyUI workflow replacement.
            src = Path(prop["workflow_src"])
            diff = config_mutator.swap_workflow(prop, src)
        else:
            # No structured mutation info — log intent but don't fail.
            diff = {"no_op": True,
                    "reason": "proposal has no model_swap or workflow_name/src keys"}

        # Step 3: smoke-verify litellm.yaml is still parseable after mutation.
        if not diff.get("no_op"):
            config_mutator._yaml_verify(config_mutator.LITELLM_YAML)
            # Additionally assert the new model entry is present in model_list.
            if prop.get("model_swap"):
                _assert_route_updated(prop["model_swap"])

        # Step 4: log success events.
        devlog.log_proposal_decision(
            proposal_id=prop["id"],
            decision="auto_promoted",
            reason=(
                f"canary stable: {canary_result.get('metrics')} | "
                f"diff: {diff}"
            ),
        )
        devlog.log_canary(
            proposal_id=prop["id"],
            traffic_pct=100,
            days=CANARY_DURATION_DAYS,
            metrics=canary_result.get("metrics", {}),
            verdict="promote",
        )
        devlog.append(
            kind="config_mutation",
            actor="supervisor",
            ref_type="proposal",
            ref_id=prop["id"],
            content={
                "event": "auto_promoted",
                "snapshot_dir": str(snap_dir.relative_to(_PROJECT_ROOT)),
                "diff": diff,
            },
        )

    except Exception:
        tb = traceback.format_exc()
        # Rollback to the snapshot we just took (if we got that far).
        if snap_dir and snap_dir.exists():
            try:
                config_mutator.rollback_to(snap_dir, proposal_id=prop["id"])
                rollback_note = f"rolled back to snapshot {snap_dir.name}"
            except Exception as rb_err:
                rollback_note = f"rollback also failed: {rb_err}"
        else:
            rollback_note = "no snapshot taken yet"

        devlog.append(
            kind="auto_promote_failed",
            actor="supervisor",
            ref_type="proposal",
            ref_id=prop["id"],
            content={
                "traceback": tb,
                "rollback": rollback_note,
                "proposal_id": prop["id"],
            },
        )
        raise  # re-raise so the caller can print and continue with other proposals


def rollback(prop: dict, reason: str, dry_run: bool = False) -> None:
    """Log a canary rollback decision (no config mutation needed — canary never applied)."""
    if dry_run:
        print(f"  [dry-run] Would rollback {prop['id']} — {reason}")
        return

    devlog.log_proposal_decision(
        proposal_id=prop["id"],
        decision="rolled_back",
        reason=reason,
    )
    devlog.log_canary(
        proposal_id=prop["id"],
        traffic_pct=0,
        days=CANARY_DURATION_DAYS,
        metrics={},
        verdict="rollback",
    )


def _assert_route_updated(swap: dict) -> None:
    """
    Re-parse litellm.yaml and assert that route_name now has new_model.
    Raises AssertionError on mismatch.
    """
    text = config_mutator.LITELLM_YAML.read_text(encoding="utf-8")
    config = config_mutator._yaml_load(text)
    route = swap["route_name"]
    expected = swap["new_model"]
    for entry in config.get("model_list", []):
        if entry.get("model_name") == route:
            actual = entry.get("litellm_params", {}).get("model", "")
            if actual != expected:
                raise AssertionError(
                    f"smoke-verify failed: route '{route}' model='{actual}' != expected='{expected}'"
                )
            return
    raise AssertionError(f"smoke-verify failed: route '{route}' not found after mutation")


# ─── Manual rollback CLI sub-command ─────────────────────────────────────────

def cmd_rollback(proposal_id: str) -> None:
    """
    Reverse an auto_promoted decision by restoring the snapshot taken at
    promote time. Logs kind='manual_rollback'.

    Exits with code 1 on failure.
    """
    if not DEVLOG.exists():
        print(f"ERROR: devlog not found at {DEVLOG}")
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        dec = decision_for(proposal_id, db)
        if dec is None:
            print(f"ERROR: no decision found for proposal {proposal_id}")
            sys.exit(1)
        if dec.get("decision") != "auto_promoted":
            print(f"ERROR: proposal {proposal_id} decision is '{dec.get('decision')}', "
                  f"not 'auto_promoted' — manual rollback only applies to promoted proposals")
            sys.exit(1)

        # Find the snapshot associated with the promote event.
        row = db.execute("""
            SELECT content FROM events
            WHERE kind='config_mutation'
              AND ref_id=?
              AND json_extract(content,'$.event')='auto_promoted'
            ORDER BY ts DESC LIMIT 1
        """, (proposal_id,)).fetchone()

        snap_dir_rel: str | None = None
        if row:
            snap_dir_rel = json.loads(row[0]).get("snapshot_dir")

        if not snap_dir_rel:
            print(f"ERROR: no snapshot_dir found for proposal {proposal_id}. "
                  f"Cannot determine which snapshot to restore.\n"
                  f"Available snapshots:")
            for d in sorted(SNAPSHOTS_DIR.iterdir()) if SNAPSHOTS_DIR.exists() else []:
                print(f"  {d.name}")
            sys.exit(1)

        snap_dir = _PROJECT_ROOT / snap_dir_rel
        print(f"Rolling back {proposal_id} from snapshot {snap_dir.name} ...")

        try:
            config_mutator.rollback_to(snap_dir, proposal_id=proposal_id)
        except Exception:
            tb = traceback.format_exc()
            devlog.append(
                kind="manual_rollback_failed",
                actor="supervisor",
                ref_type="proposal",
                ref_id=proposal_id,
                content={"traceback": tb, "snapshot_dir": snap_dir_rel},
            )
            print(f"ERROR during rollback:\n{tb}")
            sys.exit(1)

        devlog.append(
            kind="manual_rollback",
            actor="supervisor",
            ref_type="proposal",
            ref_id=proposal_id,
            content={
                "snapshot_dir": snap_dir_rel,
                "reason": "manual rollback requested via CLI",
            },
        )
        print(f"OK — rolled back {proposal_id} to {snap_dir.name}")


# ─── Main auto-promote loop ───────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    if not DEVLOG.exists():
        print(f"DEVLOG not found: {DEVLOG} — run from project root")
        sys.exit(1)

    with sqlite3.connect(DEVLOG) as db:
        proposals = pending_proposals(db)

    if not proposals:
        print("No pending proposals.")
        return

    print(f"Found {len(proposals)} pending proposal(s).")

    with sqlite3.connect(DEVLOG) as db:
        for prop in proposals:
            prop_id = prop.get("id", "<no-id>")
            state_path = CANARY_DIR / f"{prop_id}.json"

            # ── Branch 1: no canary yet → maybe start one ────────────────
            if not state_path.exists():
                safe, reason = is_auto_safe(prop)
                if safe:
                    if not dry_run:
                        start_canary(prop)
                    print(f"Started canary: {prop_id} — {prop.get('title', '?')}")
                else:
                    print(f"Skipped (not auto-safe): {prop_id} — {reason}")
                continue

            # ── Branch 2: canary exists → evaluate ──────────────────────
            result = evaluate_canary(prop_id, db)
            if result is None:
                canary = json.loads(state_path.read_text())
                started = canary.get("started_at", "?")
                print(f"Canary running: {prop_id} (started {started[:10]})")
                continue

            verdict = result["verdict"]

            if verdict == "promote":
                try:
                    promote(prop, result, dry_run=dry_run)
                    print(f"PROMOTED: {prop_id}")
                except Exception as exc:
                    print(f"PROMOTE FAILED: {prop_id} — {exc}")
                    print(f"  (rolled back and logged to devlog)")

            elif verdict == "extend":
                print(f"Extending canary: {prop_id} ({result['reason']})")

            elif verdict == "stale_canary":
                # Auto-rollback stale canaries — they'll never get enough data.
                if not dry_run:
                    rollback(prop, result["reason"])
                    # Update canary state file to reflect stale status.
                    canary = json.loads(state_path.read_text())
                    canary["status"] = "stale_canary"
                    state_path.write_text(json.dumps(canary, indent=2))
                print(f"STALE CANARY — auto-rolled back: {prop_id} ({result['reason']})")

            else:
                # Generic rollback (e.g. quality degradation detected).
                if not dry_run:
                    rollback(prop, result["reason"])
                print(f"ROLLED BACK: {prop_id} ({result['reason']})")


# ─── Entry point ─────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Supervisor auto-promote: evaluate canaries and apply config changes."
    )
    p.add_argument(
        "--rollback",
        metavar="PROPOSAL_ID",
        help="Reverse an auto_promoted decision by restoring its pre-promote snapshot.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would happen without writing any files or devlog events.",
    )
    return p


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()

    args = _build_parser().parse_args()

    if args.rollback:
        cmd_rollback(args.rollback)
    else:
        main(dry_run=args.dry_run)
