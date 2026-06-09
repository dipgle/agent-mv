"""
ComfyUI API client — submit workflow + poll until done + fetch output.

ComfyUI exposes a simple HTTP + WebSocket API:
  POST /prompt          → submit workflow
  GET  /history/{id}    → check status
  GET  /view?filename=… → download generated file

Workflows live in workflows/<name>.json (API format, exported from ComfyUI UI).
We patch input parameters into specific node IDs before submission.
"""

from __future__ import annotations
import os
import time
import json
import uuid
import shutil
from pathlib import Path
from typing import Any

import requests

from . import devlog

COMFY_BASE = os.environ.get("COMFY_BASE_URL", "http://localhost:8188")
WORKFLOW_DIR = Path("workflows")


class ComfyError(Exception): ...


def _check_workflow_real(wf_path: Path) -> None:
    """Refuse to run if file is still a stub."""
    if wf_path.suffix == ".stub" or wf_path.name.endswith(".json.stub"):
        raise ComfyError(
            f"{wf_path} is a stub. Replace with real workflow JSON exported "
            f"from ComfyUI UI (File > Save (API Format)). See workflows/README.md"
        )
    data = json.loads(wf_path.read_text())
    if isinstance(data, dict) and data.get("__stub__"):
        raise ComfyError(
            f"{wf_path} contains stub marker '__stub__'. Replace with real workflow."
        )


def load_workflow(name: str) -> dict:
    """Load workflow JSON by short name (e.g. 'flux_keyframe')."""
    candidates = [
        WORKFLOW_DIR / f"{name}.json",
        WORKFLOW_DIR / f"{name}.json.stub",
    ]
    for p in candidates:
        if p.exists():
            _check_workflow_real(p)
            return json.loads(p.read_text())
    raise FileNotFoundError(f"No workflow found for: {name} (tried {candidates})")


def patch(workflow: dict, node_id: str, key: str, value: Any) -> dict:
    """Set an input on a specific node id. Returns mutated workflow."""
    if node_id not in workflow:
        raise ComfyError(f"Node id '{node_id}' not in workflow")
    workflow[node_id]["inputs"][key] = value
    return workflow


def submit(workflow: dict, client_id: str | None = None) -> str:
    """Submit workflow. Returns prompt_id for polling."""
    client_id = client_id or str(uuid.uuid4())
    r = requests.post(
        f"{COMFY_BASE}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait(prompt_id: str, poll_s: float = 1.0, timeout_s: float = 1200) -> dict:
    """Poll until workflow completes or times out. Returns history entry."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(f"{COMFY_BASE}/history/{prompt_id}", timeout=10)
        if r.ok:
            data = r.json()
            if prompt_id in data:
                return data[prompt_id]
        time.sleep(poll_s)
    raise ComfyError(f"Workflow {prompt_id} timed out after {timeout_s}s")


def fetch_output(history: dict, dest_path: Path) -> Path:
    """Pull first output file from history into dest_path."""
    outputs = history.get("outputs", {})
    for _node_id, node_out in outputs.items():
        for kind in ("images", "videos", "audios", "files"):
            for item in node_out.get(kind, []):
                filename = item["filename"]
                subfolder = item.get("subfolder", "")
                ftype = item.get("type", "output")
                url = f"{COMFY_BASE}/view"
                r = requests.get(
                    url,
                    params={"filename": filename, "subfolder": subfolder, "type": ftype},
                    stream=True,
                    timeout=120,
                )
                r.raise_for_status()
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with dest_path.open("wb") as f:
                    shutil.copyfileobj(r.raw, f)
                return dest_path
    raise ComfyError(f"No output found in history: {history}")


def run(
    workflow_name: str,
    patches: dict[str, dict[str, Any]],
    output_path: str | Path,
    *,
    role: str,
    feature_id: str = "",
    shot_idx: int | None = None,
    modality: str = "image",
) -> Path:
    """
    High-level helper:
      1. Load workflow JSON
      2. Apply patches {node_id: {input_key: value}}
      3. Submit + wait + fetch output
      4. Log model_run + asset
    """
    t0 = time.time()
    wf = load_workflow(workflow_name)
    for nid, kvs in patches.items():
        for k, v in kvs.items():
            patch(wf, nid, k, v)

    prompt_id = submit(wf)
    history = wait(prompt_id)
    out = fetch_output(history, Path(output_path))
    latency_ms = int((time.time() - t0) * 1000)

    devlog.log_model_run(
        role=role,
        model=f"comfy/{workflow_name}",
        prompt=json.dumps(patches),
        output_ref=str(out),
        latency_ms=latency_ms,
        modality=modality,
        channel="comfy",
        feature_id=feature_id,
        shot_idx=shot_idx,
    )
    devlog.log_asset(
        feature_id=feature_id,
        asset_type=modality,
        path=str(out),
        shot_idx=shot_idx,
        size_bytes=out.stat().st_size if out.exists() else 0,
    )
    return out


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", required=True, help="workflow name (no .json)")
    ap.add_argument("--params", required=True, help="JSON: {node_id: {key: val}}")
    ap.add_argument("--out", default="./out/smoke.png")
    args = ap.parse_args()

    patches = json.loads(args.params)
    p = run(args.workflow, patches, args.out,
            role="smoke", feature_id="SMOKE", modality="image")
    print(f"OK: {p}")
