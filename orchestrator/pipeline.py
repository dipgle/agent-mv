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

Reads:
  infra/litellm.yaml   - model routing (proxy on :4000)
  workflows/*.json     - ComfyUI workflows per modality
  .env                 - API keys (optional, cloud escalation)

Writes:
  logs/devlog.sqlite   - every step logged via lib/devlog.py
  out/<feature_id>/    - assets + final.mp4
"""

from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add orchestrator/ to sys.path so `from lib import ...` works
sys.path.insert(0, str(Path(__file__).parent))

from lib import (devlog, litellm_client, comfy_client,
                 eval_tier1, eval_brand, eval_hook, eval_tier2, eval_aggregate)


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
    """Generate 1 keyframe PNG via Flux workflow."""
    out_path = out_dir / "shots" / f"{shot['idx']:02d}_keyframe.png"
    # NODE IDs depend on your specific Flux workflow.
    # Customize this mapping after exporting workflow from ComfyUI UI.
    patches = {
        # Example mapping — adjust to match your workflow's node IDs:
        # "6": {"text": shot["image_prompt"]},   # CLIPTextEncode
        # "5": {"width": width, "height": height},  # EmptyLatentImage
        # "3": {"seed": shot["idx"] * 1000},     # KSampler
    }
    return comfy_client.run(
        "flux_keyframe", patches, out_path,
        role="executor-keyframe", feature_id=feature_id,
        shot_idx=shot["idx"], modality="image",
    )


def executor_motion(shot: dict, keyframe: Path, feature_id: str,
                    out_dir: Path, fps: int = 24) -> Path:
    """Image-to-video via LTX workflow."""
    out_path = out_dir / "shots" / f"{shot['idx']:02d}_clip.mp4"
    num_frames = int(shot["duration_s"] * fps)
    patches = {
        # "loadimage_node": {"image": str(keyframe)},
        # "text_node": {"text": shot["motion"]},
        # "video_node": {"num_frames": num_frames, "fps": fps},
    }
    return comfy_client.run(
        "ltx_motion", patches, out_path,
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
    """BGM via Stable Audio Open."""
    out_path = out_dir / "bgm.wav"
    prompt = f"{', '.join(brief.get('mood', []))} {brief.get('bpm', 120)} BPM"
    patches = {
        # "sao_node": {"prompt": prompt, "duration_s": duration}
    }
    return comfy_client.run(
        "stable_audio_music", patches, out_path,
        role="executor-music", feature_id=feature_id,
        modality="audio",
    )


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


def compose(feature_dir: Path, feature_id: str) -> Path:
    """ffmpeg compose via shell script (compose.sh / compose.ps1)."""
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
    return final


# ─── Role: Reviewer — 4-tier evaluation pipeline ─────────────────────────
def reviewer(final: Path, shotlist: dict, brand: dict, feature_id: str,
             transcript: str = "", target_aspect: str = "9:16",
             allow_paid_panel: bool = False) -> dict:
    """
    Multi-tier reviewer.

    Tier 1 — deterministic checkers (ffprobe, LUFS, freeze, scene, palette)
    Tier 1' — brand auto (aspect, logo safe area, do_not_use scan)
    Tier 1'' — hook scorer (3-second dedicated)
    Tier 2 — LLM panel ensemble (3-4 models in parallel; trim-mean aggregate)
    Aggregate — per-dimension verdict; never a single overall score
    """
    tier1 = eval_tier1.evaluate(final, brand, feature_id).as_dict()
    if tier1["critical_fails"]:
        # Short-circuit — don't burn LLM cost if Tier 1 already rejects
        verdict = {
            "verdict": "rejected",
            "blocker_dimensions": tier1["critical_fails"],
            "tier1": tier1,
            "tier2_skipped": "tier1 critical fail",
        }
        devlog.append("eval_verdict", "supervisor", "feature", feature_id, verdict)
        return verdict

    brand_auto = eval_brand.evaluate(final, brand, transcript, target_aspect, feature_id)
    hook = eval_hook.evaluate(final, feature_id)
    tier2 = eval_tier2.evaluate(shotlist, brand, transcript, tier1, hook,
                                feature_id, allow_paid=allow_paid_panel)
    return eval_aggregate.aggregate(tier1, hook, brand_auto, tier2, feature_id)


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
    args = ap.parse_args()

    # Resolve resolution from aspect
    w, h = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (1024, 1024)}[args.aspect]

    feature_dir = Path(args.out) / args.feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    (feature_dir / "shots").mkdir(exist_ok=True)

    brand_path = Path(args.brand)
    brand = json.loads(brand_path.read_text()) if brand_path.exists() else {}

    print(f"[1/4] Researcher: {args.feature_id}")
    refs = researcher(args.intent, args.feature_id, feature_dir)

    print(f"[2/4] Planner: generating shotlist...")
    plan = planner(args.intent, refs, brand, args.feature_id,
                   args.duration, args.aspect)
    (feature_dir / "shotlist.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False)
    )

    for attempt in range(args.max_iter):
        print(f"[3/4] Executor (attempt {attempt + 1}): rendering shots...")
        for shot in plan["shotlist"]:
            print(f"  shot {shot['idx']}: keyframe")
            kf = executor_keyframe(shot, args.feature_id, feature_dir, w, h)
            print(f"  shot {shot['idx']}: motion")
            executor_motion(shot, kf, args.feature_id, feature_dir, args.fps)

        voice_script = "\n".join(s["voiceover"] for s in plan["shotlist"])
        print("  voice / music / captions ...")
        voice = executor_voice(voice_script, brand, args.feature_id, feature_dir)
        executor_music(plan["music_brief"], args.duration, args.feature_id, feature_dir)
        executor_caption(voice, args.feature_id, feature_dir)

        print("[compose] ffmpeg...")
        final = compose(feature_dir, args.feature_id)

        print(f"[4/4] Reviewer (4-tier eval)...")
        transcript_path = feature_dir / "subs.srt"
        transcript = transcript_path.read_text() if transcript_path.exists() else voice_script
        critique = reviewer(
            final=final,
            shotlist=plan,
            brand=brand,
            feature_id=args.feature_id,
            transcript=transcript,
            target_aspect=args.aspect,
        )
        (feature_dir / "critique.json").write_text(
            json.dumps(critique, indent=2, ensure_ascii=False)
        )

        v = critique.get("verdict", "rejected")
        score = critique.get("tier2_overall_score", "n/a")
        blockers = critique.get("blocker_dimensions", [])
        print(f"  verdict={v}  score={score}  blockers={blockers}")

        if v == "approved":
            print(f"\n[OK] APPROVED  Output: {final}")
            return
        if v == "needs_adjudicator":
            print(f"\n[ADJ] Panel disagreed — Tier 3 frontier adjudicator needed")
            devlog.log_decision("orchestrator", args.feature_id,
                                decision="adjudicator_needed",
                                rationale=f"panel sigma exceeded "
                                          f"on {critique.get('shot_issues',[])}")
            return
        print(f"\n[X] REJECTED — re-render shots with critical issues")

    print(f"\n[!] Max retries reached. Adjudicator needed.")
    devlog.log_decision("orchestrator", args.feature_id,
                        decision="adjudicator_needed",
                        rationale=f"{args.max_iter} reviewer rejects")


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
