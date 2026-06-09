"""
config_mutator.py — Atomic-safe config writers for the supervisor auto-promote loop.

Responsibilities:
  - snapshot_config()       : save current litellm.yaml + workflows/ to a timestamped
                              snapshot directory under eval/snapshots/; keep last 5.
  - mutate_litellm_yaml()   : apply a model-swap proposal to infra/litellm.yaml with
                              round-trip YAML (ruamel.yaml preserves comments + order).
                              Write to .tmp then atomic rename. Rollback on error.
  - swap_workflow()         : replace workflows/<name>.json with canary-tested source.
                              Backup current to .prev before swap.
  - rollback_to()           : restore litellm.yaml + all workflows from a snapshot dir.

All mutations are logged to devlog. All exceptions propagate to the caller so
auto_promote.py can catch and log them properly.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ruamel.yaml preserves block-style, comments, and key ordering on round-trip.
# Prefer ruamel; fall back to standard PyYAML only when actually needed (lazy
# import avoids ModuleNotFoundError at import time when neither is installed yet).
try:
    from ruamel.yaml import YAML as _RuamelYAML
    _ryaml = _RuamelYAML()
    _ryaml.preserve_quotes = True
    HAS_RUAMEL = True
except ImportError:
    HAS_RUAMEL = False

# _pyyaml is loaded lazily inside _yaml_load / _yaml_dump when ruamel absent.
_pyyaml: Any = None

# ─── Path constants (relative to project root) ───────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LITELLM_YAML = PROJECT_ROOT / "infra" / "litellm.yaml"
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"
SNAPSHOTS_DIR = PROJECT_ROOT / "eval" / "snapshots"
MAX_SNAPSHOTS = 5

# Import devlog lazily to avoid circular imports at module load time.
sys.path.insert(0, str(PROJECT_ROOT / "orchestrator"))
from lib import devlog  # noqa: E402


# ─── Snapshot helpers ─────────────────────────────────────────────────────────

def snapshot_config() -> Path:
    """
    Save current litellm.yaml and all workflows/*.json to a timestamped
    directory under eval/snapshots/<utc-iso-compact>/.
    Rotates old snapshots so at most MAX_SNAPSHOTS are kept.

    Returns the snapshot directory path.
    Raises OSError / IOError on failure.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap_dir = SNAPSHOTS_DIR / ts
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Copy litellm.yaml
    if LITELLM_YAML.exists():
        shutil.copy2(LITELLM_YAML, snap_dir / "litellm.yaml")

    # Copy all workflow JSON files (not stubs)
    wf_snap = snap_dir / "workflows"
    wf_snap.mkdir(exist_ok=True)
    for wf_file in WORKFLOWS_DIR.glob("*.json"):
        shutil.copy2(wf_file, wf_snap / wf_file.name)

    # Record snapshot manifest
    manifest = {
        "ts": ts,
        "litellm_yaml": str(LITELLM_YAML.relative_to(PROJECT_ROOT)),
        "workflows": [wf.name for wf in WORKFLOWS_DIR.glob("*.json")],
    }
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # Rotate: keep only MAX_SNAPSHOTS most-recent directories.
    _rotate_snapshots()

    devlog.append(
        kind="config_snapshot",
        actor="supervisor",
        ref_type="system",
        ref_id=ts,
        content={"snapshot_dir": str(snap_dir.relative_to(PROJECT_ROOT)),
                 "manifest": manifest},
    )
    return snap_dir


def _rotate_snapshots() -> None:
    """Delete oldest snapshot directories beyond MAX_SNAPSHOTS."""
    if not SNAPSHOTS_DIR.exists():
        return
    dirs = sorted(
        [d for d in SNAPSHOTS_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )
    for old in dirs[:-MAX_SNAPSHOTS]:
        shutil.rmtree(old, ignore_errors=True)


def latest_snapshot() -> Path | None:
    """Return the most recent snapshot directory, or None if none exist."""
    if not SNAPSHOTS_DIR.exists():
        return None
    dirs = sorted(
        [d for d in SNAPSHOTS_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )
    return dirs[-1] if dirs else None


# ─── litellm.yaml mutator ─────────────────────────────────────────────────────

def mutate_litellm_yaml(proposal: dict) -> dict:
    """
    Apply a model-swap described in `proposal` to infra/litellm.yaml.

    Expected proposal fields (under proposal["implementation_steps"] the caller
    may embed structured hints, but we derive action from proposal["category"]
    + proposal["model_swap"] if present):

      proposal["model_swap"] = {
          "route_name": "<litellm model_name>",  # e.g. "planner"
          "new_model":  "<new model string>",     # e.g. "ollama/qwen3:30b"
          "new_api_base": "<url>",                # optional
      }

    If proposal["model_swap"] is absent we log a no-op and return cleanly.

    Returns a diff summary dict {"route_name", "old_model", "new_model"}.
    Raises ValueError on parse failure, OSError on I/O failure.
    """
    swap = proposal.get("model_swap")
    if not swap:
        # No structured swap hint — nothing to mutate in YAML.
        return {"no_op": True, "reason": "proposal has no model_swap key"}

    route_name = swap["route_name"]
    new_model = swap["new_model"]
    new_api_base = swap.get("new_api_base")

    # ── Read current YAML ────────────────────────────────────────────────────
    raw_text = LITELLM_YAML.read_text(encoding="utf-8")
    config = _yaml_load(raw_text)

    # ── Locate and update entry ──────────────────────────────────────────────
    old_model = "<not found>"
    found = False
    for entry in config.get("model_list", []):
        if entry.get("model_name") == route_name:
            lp = entry.setdefault("litellm_params", {})
            old_model = lp.get("model", "<unknown>")
            lp["model"] = new_model
            if new_api_base:
                lp["api_base"] = new_api_base
            found = True
            break  # only touch the first matching entry

    if not found:
        raise ValueError(
            f"mutate_litellm_yaml: route '{route_name}' not found in model_list"
        )

    # ── Write atomically (tmp → rename) ─────────────────────────────────────
    tmp_path = LITELLM_YAML.with_suffix(".yaml.tmp")
    _yaml_dump(config, tmp_path)

    # Verify round-trip parse is valid before committing.
    _yaml_verify(tmp_path)

    # Atomic replace (os.replace is atomic on POSIX; near-atomic on Windows).
    os.replace(tmp_path, LITELLM_YAML)

    diff = {
        "route_name": route_name,
        "old_model": old_model,
        "new_model": new_model,
        "new_api_base": new_api_base,
    }
    devlog.append(
        kind="config_mutation",
        actor="supervisor",
        ref_type="proposal",
        ref_id=proposal.get("id", ""),
        content={"target": "litellm.yaml", "diff": diff},
    )
    return diff


# ─── Workflow JSON swapper ────────────────────────────────────────────────────

def swap_workflow(proposal: dict, src_path: Path) -> dict:
    """
    Replace workflows/<name>.json with the canary-tested src_path.

    The workflow name is derived from proposal["workflow_name"] (e.g.
    "flux_keyframe") which maps to workflows/flux_keyframe.json.

    Before replacing, backup the current file to workflows/<name>.json.prev.

    Returns {"workflow": name, "backed_up_to": ...}
    Raises ValueError if workflow_name is not in proposal,
           FileNotFoundError if src_path does not exist.
    """
    name = proposal.get("workflow_name")
    if not name:
        return {"no_op": True, "reason": "proposal has no workflow_name key"}

    dest = WORKFLOWS_DIR / f"{name}.json"
    backup = WORKFLOWS_DIR / f"{name}.json.prev"

    if not src_path.exists():
        raise FileNotFoundError(f"swap_workflow: src_path does not exist: {src_path}")

    # Validate source is parseable JSON before touching current file.
    json.loads(src_path.read_text(encoding="utf-8"))

    # Backup current (best-effort — may not exist yet).
    if dest.exists():
        shutil.copy2(dest, backup)

    # Atomic-style replace via tmp + rename.
    tmp_path = dest.with_suffix(".json.tmp")
    shutil.copy2(src_path, tmp_path)
    os.replace(tmp_path, dest)

    result = {
        "workflow": name,
        "dest": str(dest.relative_to(PROJECT_ROOT)),
        "backed_up_to": str(backup.relative_to(PROJECT_ROOT)) if backup.exists() else None,
        "src": str(src_path),
    }
    devlog.append(
        kind="config_mutation",
        actor="supervisor",
        ref_type="proposal",
        ref_id=proposal.get("id", ""),
        content={"target": f"workflows/{name}.json", "diff": result},
    )
    return result


# ─── Rollback ─────────────────────────────────────────────────────────────────

def rollback_to(snapshot_dir: Path, proposal_id: str = "") -> None:
    """
    Restore litellm.yaml + all workflows/*.json from a snapshot directory.

    Raises FileNotFoundError if snapshot_dir does not exist or is missing
    the manifest.
    """
    if not snapshot_dir.exists():
        raise FileNotFoundError(f"rollback_to: snapshot not found: {snapshot_dir}")

    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"rollback_to: manifest.json missing in {snapshot_dir}")

    manifest = json.loads(manifest_path.read_text())

    # Restore litellm.yaml
    snap_yaml = snapshot_dir / "litellm.yaml"
    if snap_yaml.exists():
        tmp = LITELLM_YAML.with_suffix(".yaml.tmp")
        shutil.copy2(snap_yaml, tmp)
        _yaml_verify(tmp)  # sanity-check snapshot is valid YAML
        os.replace(tmp, LITELLM_YAML)

    # Restore workflows
    snap_wf_dir = snapshot_dir / "workflows"
    if snap_wf_dir.exists():
        for src_wf in snap_wf_dir.glob("*.json"):
            dest_wf = WORKFLOWS_DIR / src_wf.name
            tmp_wf = dest_wf.with_suffix(".json.tmp")
            shutil.copy2(src_wf, tmp_wf)
            os.replace(tmp_wf, dest_wf)

    devlog.append(
        kind="config_rollback",
        actor="supervisor",
        ref_type="proposal",
        ref_id=proposal_id,
        content={
            "snapshot_dir": str(snapshot_dir.relative_to(PROJECT_ROOT)),
            "manifest": manifest,
        },
    )


# ─── YAML helpers ─────────────────────────────────────────────────────────────

def _get_pyyaml() -> Any:
    """Lazy-load PyYAML (fallback when ruamel.yaml is not installed)."""
    global _pyyaml
    if _pyyaml is None:
        import yaml as _yaml_module  # type: ignore
        _pyyaml = _yaml_module
    return _pyyaml


def _yaml_load(text: str) -> Any:
    """Parse YAML text, using ruamel if available for comment-preserving round-trip."""
    if HAS_RUAMEL:
        import io
        return _ryaml.load(io.StringIO(text))
    return _get_pyyaml().safe_load(text)


def _yaml_dump(config: Any, path: Path) -> None:
    """Serialize config to path, using ruamel if available."""
    if HAS_RUAMEL:
        import io
        buf = io.StringIO()
        _ryaml.dump(config, buf)
        path.write_text(buf.getvalue(), encoding="utf-8")
    else:
        path.write_text(
            _get_pyyaml().dump(config, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )


def _yaml_verify(path: Path) -> None:
    """Re-parse a YAML file to confirm it is syntactically valid."""
    text = path.read_text(encoding="utf-8")
    parsed = _yaml_load(text)
    if parsed is None:
        raise ValueError(f"_yaml_verify: '{path}' parsed to None (empty or bad YAML)")
    if not isinstance(parsed, dict) or "model_list" not in parsed:
        raise ValueError(
            f"_yaml_verify: '{path}' missing required 'model_list' key after parse"
        )
