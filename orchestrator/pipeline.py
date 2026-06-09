#!/usr/bin/env python3
"""
Video pipeline entry — 4 roles + ComfyUI executor.

Roles:
  1. Researcher: scrape references (manual or scripted)
  2. Planner:    intent + refs -> script.md + shotlist.json (LLM)
  3. Executor:   keyframe + motion + voice + music + caption + compose
  4. Reviewer:   sample frames + transcript -> JSON critique (LLM)

Usage:
    python orchestrator/pipeline.py \
        --intent "TikTok 30s giới thiệu SaaS analytics" \
        --feature-id VID-001 \
        --aspect 9:16 \
        --duration 30 \
        --brand brand-example.json

Resume / crash-recovery flags:
    --resume            (default) pick up from last successful step
    --force-redo        ignore existing checkpoint, re-render everything
    --from-step STEP_ID restart from a specific step (invalidates that step +
                        all downstream steps)
    --show-checkpoint   print checkpoint state and exit (no rendering)
    --max-crashes N     bail out after N consecutive step crashes (default 3)

Reads:
  infra/litellm.yaml   - model routing (proxy on :4000)
  workflows/*.json     - ComfyUI workflows per modality
  .env                 - API keys (optional, cloud escalation)

Writes:
  logs/devlog.sqlite              - every step logged via lib/devlog.py
  out/<feature_id>/               - assets + final.mp4
  out/<feature_id>/.checkpoint.json - resume state (updated after each step)
"""

from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add orchestrator/ to sys.path so `from lib import ...` works
sys.path.insert(0, str(Path(__file__).parent))

from lib import (devlog, litellm_client, comfy_client, stock_music,
                 eval_tier1, eval_brand, eval_hook, eval_tier2, eval_aggregate,
                 checkpoint as ckpt_lib, moderation, c2pa)
from lib.checkpoint import CheckpointStore, step_id_shot, step_id_reviewer


# ── Exception type for ComfyUI / render failures ──────────────────────────────

class StepCrashed(Exception):
    """Raised when an executor step fails with a transient error.

    The pipeline catches this, logs kind='step_crashed', and either retries
    (if max_crashes not exceeded) or bails out while leaving the checkpoint
    at the last successfully completed step.
    """


# Downstream step order used by --from-step invalidation.
# Each entry is either a literal step ID or the prefix 'shot_' meaning
# "all per-shot steps".  Steps are listed in execution order.
_STEP_ORDER = [
    "researcher",
    "planner",
    "shot_",          # placeholder: expands to all shot_NN_keyframe + shot_NN_motion
    "voice",
    "music",
    "caption",
    "compose",
    # reviewer_attempt_N is always re-run; no need to list it here
]


# ─── Role: Researcher ─────────────────────────────────────────────────────
def researcher(intent: str, feature_id: str, out_dir: Path) -> dict:
    """
    Find 5-8 reference videos. For Phase 0 this is manual — the dev populates
    out_dir/reference.json with URLs + notes. Later: scripted scrape + VL parse.
    """
    ref_path = out_dir / "reference.json"
    if ref_path.exists():
        return json.loads(ref_path.read_text())

    # Stub default; LLM Researcher will be added in Phase 1
    refs = {
        "intent": intent,
        "samples": [],
        "pacing_avg_shot_s": 2.5,
        "common_hooks": [],
        "note": "Researcher in stub mode. Edit reference.json manually or "
                "implement VL scrape in lib/researcher.py.",
    }
    ref_path.write_text(json.dumps(refs, indent=2, ensure_ascii=False))
    devlog.append("artifact", "researcher", "feature", feature_id,
                  {"asset_type": "reference", "path": str(ref_path)})
    return refs


# ─── Role: Planner ────────────────────────────────────────────────────────
PLANNER_SYSTEM = """You are the Planner in a video production pipeline.
Output strict JSON matching this schema:
{
  "script_md": "<markdown script with 3-act: hook 0-3s, body, CTA>",
  "shotlist": [
    {
      "idx": 1,
      "duration_s": 2.5,
      "image_prompt": "<for Flux text-to-image>",
      "motion": "<for LTX image-to-video>",
      "voiceover": "<line spoken during this shot>",
      "overlay": "<optional text overlay>"
    }
  ],
  "music_brief": {"bpm": 120, "mood": "energetic", "key": "C major"}
}
"""


def planner(intent: str, refs: dict, brand: dict, feature_id: str,
            duration: int, aspect: str) -> dict:
    """Call LLM planner with intent + brand + refs. Output to shotlist.json."""
    prompt = f"""Create shotlist for {duration}s video.
Aspect: {aspect}
Intent: {intent}
Brand: {json.dumps(brand, ensure_ascii=False)}
Reference patterns: {json.dumps(refs.get("common_hooks", []), ensure_ascii=False)}

Constraints:
- {duration}s total
- shot duration 2-4s each
- voiceover total ~85% of duration
- music BPM in range {brand.get('music_bpm_range', [100, 130])}
- voice_tone: {brand.get('voice_tone', 'neutral')}
"""
    result, _ = litellm_client.call_json(
        role="planner",
        model="planner",  # logical name from litellm.yaml
        prompt=prompt,
        system=PLANNER_SYSTEM,
        feature_id=feature_id,
    )
    return result


# ─── Role: Executor (modality-split) ──────────────────────────────────────
def executor_keyframe(shot: dict, feature_id: str, out_dir: Path,
                      width: int, height: int) -> Path:
    """Generate 1 keyframe PNG via FLUX.1-schnell workflow (Apache 2.0).

    FLUX.1-schnell is a 4-step distilled model: set steps=4, cfg=0.0 in the
    KSampler node. A negative prompt has no effect and should be left empty.
    """
    out_path = out_dir / "shots" / f"{shot['idx']:02d}_keyframe.png"
    # NODE IDs depend on your specific workflow export from ComfyUI UI.
    # Customize after exporting workflows/flux_schnell_keyframe.json.
    # Key Schnell-specific settings: steps=4, cfg=0.0, scheduler='simple'.
    patches = {
        # Example mapping — adjust to match your workflow's node IDs:
        # "6": {"text": shot["image_prompt"]},     # CLIPTextEncode
        # "5": {"width": width, "height": height},  # EmptyLatentImage
        # "3": {"seed": shot["idx"] * 1000,         # KSampler
        #        "steps": 4, "cfg": 0.0,
        #        "sampler_name": "euler",
        #        "scheduler": "simple"},
    }
    return comfy_client.run(
        "flux_schnell_keyframe", patches, out_path,
        role="executor-keyframe", feature_id=feature_id,
        shot_idx=shot["idx"], modality="image",
    )


def executor_motion(shot: dict, keyframe: Path, feature_id: str,
                    out_dir: Path, fps: int = 24) -> Path:
    """Image-to-video via Wan2.1-T2V-14B workflow (Apache 2.0).

    Wan2.1 replaces LTX-Video (research-only license). It requires more VRAM
    (~24-40 GB depending on resolution) but produces higher-quality motion.
    For proxy renders at 720p, VRAM requirement is ~24 GB.
    """
    out_path = out_dir / "shots" / f"{shot['idx']:02d}_clip.mp4"
    num_frames = int(shot["duration_s"] * fps)
    patches = {
        # NODE IDs depend on your specific Wan2.1 workflow export from ComfyUI UI.
        # Customize after exporting workflows/wan21_motion.json.
        # "loadimage_node": {"image": str(keyframe)},   # LoadImage node
        # "text_node": {"text": shot["motion"]},        # text prompt node
        # "video_node": {"num_frames": num_frames,      # WanVideoSampler or equivalent
        #                "fps": fps},
    }
    return comfy_client.run(
        "wan21_motion", patches, out_path,
        role="executor-motion", feature_id=feature_id,
        shot_idx=shot["idx"], modality="video",
    )


def executor_voice(script: str, brand: dict, feature_id: str,
                   out_dir: Path) -> Path:
    """Voiceover via F5-TTS."""
    out_path = out_dir / "voice.wav"
    ref = brand.get("voice_reference", {})
    patches = {
        # "f5_node": {
        #     "text": script,
        #     "ref_audio": ref.get("wav_path", ""),
        #     "ref_text": ref.get("transcript", ""),
        # }
    }
    return comfy_client.run(
        "f5_tts", patches, out_path,
        role="executor-voice", feature_id=feature_id,
        modality="audio",
    )


def executor_music(brief: dict, duration: int, feature_id: str,
                   out_dir: Path) -> Path:
    """Download royalty-free BGM from Pixabay API or CC0 fallback library.

    Replaces Stable Audio Open (CC-BY-NC, non-commercial). The new approach
    sources pre-made music that is commercially cleared rather than generating
    it on-device.

    Signature is unchanged from the Stable Audio version so all callers continue
    to work without modification.

    The track is trimmed or looped to `duration` seconds via ffmpeg.
    License info is included in the devlog kind='artifact' event metadata.
    """
    import subprocess
    import time

    out_path = out_dir / "bgm.wav"
    client = stock_music.PixabayMusicClient()

    # select_track logs a kind='stock_music_pick' event internally.
    t0 = time.time()
    source_path = client.select_track(brief, feature_id=feature_id)
    latency_ms = int((time.time() - t0) * 1000)

    # Trim / loop to target duration using ffmpeg.
    # -t trims; -stream_loop -1 loops infinitely before trim so short tracks work.
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",     # loop input if shorter than target
        "-i", str(source_path),
        "-t", str(duration),      # trim to exact duration
        "-af", "afade=t=out:st={},d=2".format(max(0, duration - 2)),  # fade out last 2s
        "-ar", "44100",           # normalize sample rate
        "-ac", "2",               # stereo
        str(out_path),
    ]
    subprocess.check_call(ffmpeg_cmd,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Log as artifact with license metadata so the cost rollup and audit
    # pipelines can verify commercial compliance.
    devlog.append(
        "artifact", "executor-music", "feature", feature_id,
        {
            "asset_type": "music",
            "path": str(out_path),
            "source_path": str(source_path),
            "duration_s": duration,
            "latency_ms": latency_ms,
            "license": "Pixabay License (royalty-free, commercial OK) or CC0 Public Domain",
            "license_compliant": True,
        },
    )
    return out_path


def executor_caption(voice: Path, feature_id: str, out_dir: Path,
                     language: str = "vi") -> Path:
    """Generate SRT subtitles via Whisper."""
    out_path = out_dir / "subs.srt"
    patches = {
        # "whisper_node": {"audio": str(voice), "language": language}
    }
    return comfy_client.run(
        "whisper_caption", patches, out_path,
        role="executor-caption", feature_id=feature_id,
        modality="audio",
    )


def _resolve_compose_cmd(feature_dir: Path) -> list[str]:
    """Pick the right shell + script per OS. Robust to PowerShell variant."""
    import shutil
    arg = str(feature_dir)
    if sys.platform != "win32":
        return ["bash", "scripts/compose.sh", arg]
    # Windows: prefer pwsh (PowerShell Core 7+); fall back to legacy powershell.exe
    if shutil.which("pwsh"):
        return ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", "scripts/compose.ps1", arg]
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", "scripts/compose.ps1", arg]


def compose(feature_dir: Path, feature_id: str,
            brand: dict | None = None) -> Path:
    """ffmpeg compose via shell script (compose.sh / compose.ps1).

    After ffmpeg writes final.mp4, embeds C2PA Content Credentials manifest
    so every produced video carries AI-disclosure metadata required by EU AI
    Act Article 50 and platform policies.  If c2pa-python is not installed the
    step is skipped with a logged warning — it never blocks the render.
    """
    import time
    t0 = time.time()
    cmd = _resolve_compose_cmd(feature_dir)
    subprocess.check_call(cmd)
    final = feature_dir / "final.mp4"
    devlog.log_model_run(
        role="compose", model="local/ffmpeg",
        prompt=str(feature_dir), output_ref=str(final),
        latency_ms=int((time.time() - t0) * 1000),
        modality="multi", channel="local",
        feature_id=feature_id,
    )
    devlog.log_asset(feature_id, "final", str(final))

    # ── C2PA Content Credentials embed ─────────────────────────────────────
    # Build a manifest with the model stack sourced from brand if available.
    model_stack = (brand or {}).get("model_stack", [])
    manifest = c2pa.build_manifest(
        feature_id=feature_id,
        model_stack=model_stack,
    )
    c2pa.embed_credentials(final, manifest)
    # embed_credentials logs its own devlog event; failure is non-fatal.

    return final


# ─── Role: Reviewer — 4-tier evaluation pipeline ─────────────────────────
def reviewer(final: Path, shotlist: dict, brand: dict, feature_id: str,
             transcript: str = "", target_aspect: str = "9:16",
             allow_paid_panel: bool = False) -> dict:
    """
    Multi-tier reviewer.

    Tier 0 — Content moderation gate (NSFW, real-person, trademark, consent)
              Runs BEFORE Tier 1 for critical blocks; Tier 2 receives major flags.
    Tier 1 — deterministic checkers (ffprobe, LUFS, freeze, scene, palette)
    Tier 1' — brand auto (aspect, logo safe area, do_not_use scan)
    Tier 1'' — hook scorer (3-second dedicated)
    Tier 2 — LLM panel ensemble (3-4 models in parallel; trim-mean aggregate)
    Aggregate — per-dimension verdict; never a single overall score
    """
    # ── Tier 0: content moderation ─────────────────────────────────────────
    # Sample frames from the final video for visual checks; fall back to any
    # existing keyframe PNGs in shots/ if ffmpeg frame extraction fails.
    sampled = moderation.sample_frames_from_video(final, n=6)
    if not sampled:
        # Fallback: use keyframe PNGs already on disk
        shots_dir = final.parent / "shots"
        sampled = sorted(shots_dir.glob("*_keyframe.png"))[:6] if shots_dir.exists() else []

    mod_result = moderation.evaluate(final, sampled, brand, feature_id)
    agg = mod_result.get("aggregate", {})

    if agg.get("has_critical"):
        # Critical moderation failure — reject immediately, skip all LLM cost.
        verdict = {
            "verdict": "rejected",
            "blocker": "moderation_critical",
            "blocker_dimensions": agg.get("critical_checks", []),
            "moderation": mod_result,
            "tier1_skipped": "moderation_critical",
            "tier2_skipped": "moderation_critical",
        }
        devlog.append("eval_verdict", "moderation", "feature", feature_id, verdict)
        return verdict

    # Build a moderation critique entry to pass into Tier 2 context so the
    # LLM panel is aware of any major flags (e.g. consent warnings).
    mod_critique: list[dict] = []
    if agg.get("has_major"):
        for check in agg.get("major_checks", []):
            check_data = mod_result.get(check, {})
            mod_critique.append({
                "type": "moderation",
                "severity": "major",
                "check": check,
                "categories": check_data.get("categories", []),
                "msg": str(check_data.get("details", {}).get("message", "")),
            })

    # ── Tier 1: deterministic checks ────────────────────────────────────────
    tier1 = eval_tier1.evaluate(final, brand, feature_id).as_dict()
    if tier1["critical_fails"]:
        # Short-circuit — don't burn LLM cost if Tier 1 already rejects
        verdict = {
            "verdict": "rejected",
            "blocker_dimensions": tier1["critical_fails"],
            "moderation": mod_result,
            "tier1": tier1,
            "tier2_skipped": "tier1 critical fail",
        }
        devlog.append("eval_verdict", "supervisor", "feature", feature_id, verdict)
        return verdict

    brand_auto = eval_brand.evaluate(final, brand, transcript, target_aspect, feature_id)
    hook = eval_hook.evaluate(final, feature_id)
    tier2 = eval_tier2.evaluate(shotlist, brand, transcript, tier1, hook,
                                feature_id, allow_paid=allow_paid_panel)
    result = eval_aggregate.aggregate(tier1, hook, brand_auto, tier2, feature_id)

    # Attach moderation summary to the final verdict for dashboard visibility.
    # Major moderation flags are injected into shot_issues so the aggregate
    # and the LLM panel can surface them in the critique JSON.
    result["moderation"] = mod_result
    if mod_critique:
        result.setdefault("shot_issues", []).extend(mod_critique)

    return result


def _legacy_reviewer_stub(final: Path, shotlist: dict, brand: dict,
                          feature_id: str) -> dict:
    """Kept temporarily for backwards-compatibility callers."""
    REVIEWER_SYSTEM_FALLBACK = """You are the Reviewer.  Output strict JSON
matching {verdict, overall_score, issues, suggestions}."""
    prompt = (f"Review video. Shotlist: {json.dumps(shotlist, ensure_ascii=False)[:1500]}\n"
              f"Brand: {json.dumps(brand, ensure_ascii=False)[:1000]}")
    result, _ = litellm_client.call_json(
        role="reviewer",
        model="reviewer",
        prompt=prompt,
        system=REVIEWER_SYSTEM_FALLBACK,
        feature_id=feature_id,
    )
    return result


# ─── Checkpoint-aware step executor wrapper ───────────────────────────────────

def _run_step(
    cp: CheckpointStore,
    step_id: str,
    fn,
    *args,
    force_redo: bool = False,
    feature_id: str = "",
    crash_counter: list | None = None,
    max_crashes: int = 3,
    **kwargs,
):
    """
    Execute fn(*args, **kwargs) with checkpoint skip/crash handling.

    - If step_id is already done in checkpoint AND force_redo is False, skip
      and return the cached artifact path (or None if no artifact).
    - On ComfyUI 500 / connection error: log kind='step_crashed', increment
      crash_counter, and re-raise StepCrashed so the caller can bail out.
    - On success: call cp.mark_step_done(step_id, result).

    crash_counter should be a mutable list[int] with one element so the
    caller retains visibility across multiple _run_step calls.
    """
    if crash_counter is None:
        crash_counter = [0]

    if not force_redo and cp.is_done(step_id):
        cached = cp.get_artifact(step_id)
        devlog.append(
            "step_skipped", "orchestrator", "feature", feature_id,
            {"step_id": step_id, "reason": "checkpoint",
             "artifact": str(cached) if cached else None},
        )
        print(f"  [skip] {step_id}  (checkpoint hit)")
        return cached

    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        tb = traceback.format_exc()
        crash_counter[0] += 1
        devlog.append(
            "step_crashed", "orchestrator", "feature", feature_id,
            {
                "step_id": step_id,
                "error_class": type(exc).__name__,
                "traceback": tb,
                "crash_n": crash_counter[0],
            },
        )
        print(f"  [CRASH #{crash_counter[0]}] {step_id}: {exc}")
        if crash_counter[0] >= max_crashes:
            print(f"  [!] max_crashes={max_crashes} reached — bailing out. "
                  f"Checkpoint at last completed step; re-run to resume.")
        raise StepCrashed(f"{step_id}: {exc}") from exc

    # Record completion; result may be a Path or None.
    artifact = str(result) if result is not None else None
    cp.mark_step_done(step_id, artifact_path=artifact)
    return result


def _invalidate_downstream(
    cp: CheckpointStore,
    from_step: str,
    all_shot_ids: list[str],
) -> None:
    """
    Invalidate from_step and every step that logically comes after it.

    Uses the _STEP_ORDER list; 'shot_' prefix matches all per-shot steps.
    """
    found = False
    to_invalidate: list[str] = []

    for entry in _STEP_ORDER:
        if not found:
            if entry == "shot_":
                if any(from_step == sid for sid in all_shot_ids):
                    found = True
                    to_invalidate.extend(all_shot_ids)
            elif entry == from_step:
                found = True
                to_invalidate.append(from_step)
        else:
            if entry == "shot_":
                to_invalidate.extend(all_shot_ids)
            else:
                to_invalidate.append(entry)

    # Also handle the case where from_step is itself a shot step (e.g.
    # "shot_03_keyframe") — invalidate that shot and everything after it.
    if not found and from_step in all_shot_ids:
        idx = all_shot_ids.index(from_step)
        to_invalidate.extend(all_shot_ids[idx:])
        # Everything after shots
        for entry in _STEP_ORDER:
            if entry not in ("shot_", "researcher", "planner"):
                to_invalidate.append(entry)

    if to_invalidate:
        cp.invalidate_steps(to_invalidate)
        print(f"  [--from-step] invalidated: {to_invalidate}")


# ─── Main ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Video production pipeline")
    ap.add_argument("--intent", required=True, help="Free-text video intent")
    ap.add_argument("--feature-id", required=True, help="Unique ID, e.g. VID-001")
    ap.add_argument("--aspect", default="9:16", choices=["9:16", "16:9", "1:1"])
    ap.add_argument("--duration", type=int, default=30, help="Total seconds")
    ap.add_argument("--brand", default="brand-example.json")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--max-iter", type=int, default=3,
                    help="Reviewer reject -> re-execute, max retries")

    # ── Checkpoint / resume flags ─────────────────────────────────────────
    resume_group = ap.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--resume", dest="resume", action="store_true", default=True,
        help="(default) Resume from the last completed step if a checkpoint exists",
    )
    resume_group.add_argument(
        "--force-redo", dest="force_redo", action="store_true", default=False,
        help="Ignore existing checkpoint and re-render all steps from scratch",
    )
    resume_group.add_argument(
        "--show-checkpoint", dest="show_checkpoint", action="store_true",
        default=False,
        help="Print checkpoint state and exit without rendering",
    )
    ap.add_argument(
        "--from-step", dest="from_step", default=None, metavar="STEP_ID",
        help="Restart from a specific step, invalidating that step and all "
             "downstream steps (e.g. --from-step shot_03_keyframe)",
    )
    ap.add_argument(
        "--max-crashes", dest="max_crashes", type=int, default=3,
        help="Bail out after N consecutive step crashes in a single run "
             "(default 3); checkpoint is preserved for the next invocation",
    )

    args = ap.parse_args()

    # Resolve resolution from aspect
    w, h = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (1024, 1024)}[args.aspect]

    feature_dir = Path(args.out) / args.feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "shots").mkdir(exist_ok=True)

    brand_path = Path(args.brand)
    brand = json.loads(brand_path.read_text()) if brand_path.exists() else {}

    # ── Checkpoint init ───────────────────────────────────────────────────
    cp = CheckpointStore.load(feature_dir, args.feature_id)

    # --show-checkpoint: print and exit
    if args.show_checkpoint:
        print(cp.pretty_print())
        return

    # --force-redo: wipe checkpoint before starting
    if args.force_redo:
        print(f"[checkpoint] --force-redo: clearing checkpoint for {args.feature_id}")
        cp.reset(scope="all")

    # Health check: warn if suspiciously many attempts
    warn = cp.high_attempt_warning()
    if warn:
        print(warn)

    # Health check: repair artifact paths that were deleted since last run
    missing_steps = cp.repair_missing()
    if missing_steps:
        for ms in missing_steps:
            devlog.append(
                "checkpoint_repair", "orchestrator", "feature", args.feature_id,
                {"step_id": ms, "reason": "artifact_missing_on_disk"},
            )
        print(f"[checkpoint] repaired {len(missing_steps)} step(s) with missing "
              f"artifacts: {missing_steps}")

    # Resume banner
    done = cp.completed_steps
    if done and not args.force_redo:
        print(f"[checkpoint] Resuming from step after: {cp._data.get('last_step')}  "
              f"(attempt {cp.attempt_n}, {len(done)} step(s) already done)")

    # Shared crash counter across this invocation (mutable list so helpers can
    # mutate it without needing to return a value).
    crash_counter: list[int] = [0]
    force_redo = args.force_redo

    # ── Step: Researcher ──────────────────────────────────────────────────
    print(f"[1/4] Researcher: {args.feature_id}")
    try:
        refs = _run_step(
            cp, "researcher",
            researcher, args.intent, args.feature_id, feature_dir,
            force_redo=force_redo,
            feature_id=args.feature_id,
            crash_counter=crash_counter,
            max_crashes=args.max_crashes,
        )
    except StepCrashed:
        return
    # If researcher was cached, refs is the Path; re-load the JSON.
    if isinstance(refs, Path):
        refs = json.loads(refs.read_text())

    # ── Step: Planner ─────────────────────────────────────────────────────
    print(f"[2/4] Planner: generating shotlist...")
    shotlist_path = feature_dir / "shotlist.json"

    def _planner_with_save():
        result = planner(args.intent, refs, brand, args.feature_id,
                         args.duration, args.aspect)
        shotlist_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        return shotlist_path

    try:
        plan_out = _run_step(
            cp, "planner",
            _planner_with_save,
            force_redo=force_redo,
            feature_id=args.feature_id,
            crash_counter=crash_counter,
            max_crashes=args.max_crashes,
        )
    except StepCrashed:
        return

    # Load plan from disk (whether freshly generated or cached).
    plan = json.loads(shotlist_path.read_text())

    # Build the canonical list of all shot step IDs for this plan so that
    # --from-step and invalidate_downstream know the full set.
    all_shot_step_ids: list[str] = []
    for shot in plan["shotlist"]:
        all_shot_step_ids.append(step_id_shot(shot["idx"], "keyframe"))
        all_shot_step_ids.append(step_id_shot(shot["idx"], "motion"))

    # Apply --from-step invalidation now that we know the full step set.
    if args.from_step:
        _invalidate_downstream(cp, args.from_step, all_shot_step_ids)

    # Per-shot re-render: track which shots need work on each attempt.
    # First attempt = all shots; subsequent attempts = only shots flagged
    # critical/major by the previous Reviewer pass.
    shots_to_render = list(plan["shotlist"])
    audio_done = cp.is_done("voice") and cp.is_done("music") and cp.is_done("caption")

    for attempt in range(args.max_iter):
        print(f"[3/4] Executor (attempt {attempt + 1}): "
              f"rendering {len(shots_to_render)} shot(s)...")

        for shot in shots_to_render:
            kf_step = step_id_shot(shot["idx"], "keyframe")
            mo_step = step_id_shot(shot["idx"], "motion")

            print(f"  shot {shot['idx']}: keyframe")
            try:
                kf = _run_step(
                    cp, kf_step,
                    executor_keyframe, shot, args.feature_id, feature_dir, w, h,
                    force_redo=force_redo,
                    feature_id=args.feature_id,
                    crash_counter=crash_counter,
                    max_crashes=args.max_crashes,
                )
            except StepCrashed:
                return

            # If keyframe was a cache hit, kf is a Path already.
            # If it was freshly rendered, kf is also a Path.
            if kf is None:
                kf = feature_dir / "shots" / f"{shot['idx']:02d}_keyframe.png"

            print(f"  shot {shot['idx']}: motion")
            try:
                _run_step(
                    cp, mo_step,
                    executor_motion, shot, kf, args.feature_id, feature_dir, args.fps,
                    force_redo=force_redo,
                    feature_id=args.feature_id,
                    crash_counter=crash_counter,
                    max_crashes=args.max_crashes,
                )
            except StepCrashed:
                return

        if not audio_done:
            voice_script = "\n".join(s["voiceover"] for s in plan["shotlist"])
            print("  voice / music / captions ...")

            try:
                voice = _run_step(
                    cp, "voice",
                    executor_voice, voice_script, brand, args.feature_id, feature_dir,
                    force_redo=force_redo,
                    feature_id=args.feature_id,
                    crash_counter=crash_counter,
                    max_crashes=args.max_crashes,
                )
            except StepCrashed:
                return

            if voice is None:
                voice = feature_dir / "voice.wav"

            try:
                _run_step(
                    cp, "music",
                    executor_music, plan["music_brief"], args.duration,
                    args.feature_id, feature_dir,
                    force_redo=force_redo,
                    feature_id=args.feature_id,
                    crash_counter=crash_counter,
                    max_crashes=args.max_crashes,
                )
            except StepCrashed:
                return

            try:
                _run_step(
                    cp, "caption",
                    executor_caption, voice, args.feature_id, feature_dir,
                    force_redo=force_redo,
                    feature_id=args.feature_id,
                    crash_counter=crash_counter,
                    max_crashes=args.max_crashes,
                )
            except StepCrashed:
                return

            audio_done = True
        else:
            voice_script = "\n".join(s["voiceover"] for s in plan["shotlist"])

        print("[compose] ffmpeg...")
        try:
            final_path = _run_step(
                cp, "compose",
                compose, feature_dir, args.feature_id,
                force_redo=force_redo,
                feature_id=args.feature_id,
                crash_counter=crash_counter,
                max_crashes=args.max_crashes,
                brand=brand,
            )
        except StepCrashed:
            return

        if final_path is None:
            final_path = feature_dir / "final.mp4"

        print(f"[4/4] Reviewer (4-tier eval)...")
        transcript_path = feature_dir / "subs.srt"
        transcript = (
            transcript_path.read_text() if transcript_path.exists() else voice_script
        )
        critique = reviewer(
            final=final_path,
            shotlist=plan,
            brand=brand,
            feature_id=args.feature_id,
            transcript=transcript,
            target_aspect=args.aspect,
        )
        (feature_dir / "critique.json").write_text(
            json.dumps(critique, indent=2, ensure_ascii=False)
        )

        # Record reviewer verdict in checkpoint so it can be inspected
        # across restarts without re-running the LLM panel.
        reviewer_step = step_id_reviewer(cp.attempt_n)
        cp.mark_step_done(
            reviewer_step,
            artifact_path=str(feature_dir / "critique.json"),
        )

        v = critique.get("verdict", "rejected")
        score = critique.get("tier2_overall_score", "n/a")
        blockers = critique.get("blocker_dimensions", [])
        print(f"  verdict={v}  score={score}  blockers={blockers}")

        if v == "approved":
            print(f"\n[OK] APPROVED  Output: {final_path}")
            return
        if v == "needs_adjudicator":
            print(f"\n[ADJ] Panel disagreed — Tier 3 frontier adjudicator needed")
            devlog.log_decision("orchestrator", args.feature_id,
                                decision="adjudicator_needed",
                                rationale=f"panel sigma exceeded "
                                          f"on {critique.get('shot_issues',[])}")
            return

        # Decide what to re-render for the next attempt — only the shots
        # that critically failed. Audio-level blockers force voice/music
        # regen too.
        shots_to_render, retry_audio = _flagged_shots(plan, critique)
        if not shots_to_render and not retry_audio:
            print(f"\n[X] REJECTED but no shot flagged for re-render — bail out")
            break

        # Invalidate only the flagged shots in the checkpoint; keep all
        # others so the next attempt skips them.
        if shots_to_render:
            flagged_step_ids: list[str] = []
            for s in shots_to_render:
                flagged_step_ids.append(step_id_shot(s["idx"], "keyframe"))
                flagged_step_ids.append(step_id_shot(s["idx"], "motion"))
            # Compose always needs to re-run when any shot changes.
            flagged_step_ids.append("compose")
            cp.invalidate_steps(flagged_step_ids)
            print(f"  Re-rendering shots: {[s['idx'] for s in shots_to_render]}")

        if retry_audio:
            cp.reset(scope="audio")
            cp.invalidate_steps(["compose"])
            audio_done = False
            print(f"  Audio-level blocker flagged → re-render voice/music/captions")

        # Advance checkpoint to the new attempt number.
        cp.next_attempt()

    print(f"\n[!] Max retries reached. Adjudicator needed.")
    devlog.log_decision("orchestrator", args.feature_id,
                        decision="adjudicator_needed",
                        rationale=f"{args.max_iter} reviewer rejects")


def _flagged_shots(plan: dict, critique: dict) -> tuple[list[dict], bool]:
    """
    Return (shots_to_re_render, retry_audio) from the Reviewer critique.

    Only critical/major shot_issues qualify; minor issues are accumulated
    but don't trigger a re-render (cost / noise floor).
    Compose-level blockers (audio_lufs, compliance) force audio regen.
    """
    shot_idx_flagged: set[int] = set()
    for issue in critique.get("shot_issues", []):
        sev = issue.get("severity", "minor")
        if sev in ("critical", "major"):
            try:
                shot_idx_flagged.add(int(issue.get("shot", -1)))
            except (TypeError, ValueError):
                pass

    retry_audio = any(b in critique.get("blocker_dimensions", [])
                      for b in ("audio_lufs", "compliance"))

    if not shot_idx_flagged:
        # Whole-video blocker that isn't shot-attributed → re-render everything
        if critique.get("blocker_dimensions"):
            return list(plan["shotlist"]), True
        return [], False

    shots = [s for s in plan["shotlist"] if s.get("idx") in shot_idx_flagged]
    return shots, retry_audio


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
