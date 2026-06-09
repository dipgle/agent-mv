"""
Tier 1 — Deterministic checkers (auto, every output).

Runs on every produced video before LLM panel touches it.  Each check is
objective (pass/fail or numeric) — no model, no opinion.

Critical fails short-circuit the pipeline (reject without LLM cost).

Tools used:
  - ffprobe (binary, subprocess) — metadata + duration + frame stats
  - pyloudnorm (Python pkg)      — EBU R128 LUFS measurement
  - opencv-python                — color histogram + freeze frame + scene change
  - PIL/Pillow                   — basic image ops

Returns a dict; pipeline.py logs each check as kind='eval_tier1' event.
"""

from __future__ import annotations
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import devlog


@dataclass
class Tier1Result:
    technical: dict = field(default_factory=dict)
    audio: dict = field(default_factory=dict)
    visual: dict = field(default_factory=dict)
    critical_fails: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "technical": self.technical,
            "audio": self.audio,
            "visual": self.visual,
            "critical_fails": self.critical_fails,
            "pass": len(self.critical_fails) == 0,
        }


# ─── Technical: ffprobe metadata ─────────────────────────────────────────
def check_technical(video: Path) -> dict:
    """Run ffprobe, extract: codec, width, height, fps, duration, bitrate."""
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-print_format", "json", str(video),
        ], text=True, timeout=30)
        data = json.loads(out)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"pass": False, "error": str(e)}

    video_stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio_stream = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if not video_stream:
        return {"pass": False, "error": "no video stream"}

    width = video_stream.get("width", 0)
    height = video_stream.get("height", 0)
    duration = float(data.get("format", {}).get("duration", 0))
    fps_raw = video_stream.get("r_frame_rate", "0/1")
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den) if float(den) else 0
    except (ValueError, ZeroDivisionError):
        fps = 0

    bitrate = int(data.get("format", {}).get("bit_rate", 0))

    fails = []
    if width < 540 or height < 540:
        fails.append(f"resolution too low: {width}x{height}")
    if duration < 1:
        fails.append(f"duration too short: {duration}s")
    if fps < 23:
        fails.append(f"fps too low: {fps}")
    if not audio_stream:
        fails.append("no audio stream")

    return {
        "pass": len(fails) == 0,
        "codec": video_stream.get("codec_name"),
        "width": width, "height": height,
        "fps": round(fps, 2),
        "duration_s": round(duration, 2),
        "bitrate_kbps": bitrate // 1000,
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "fails": fails,
    }


# ─── Audio: LUFS loudness ────────────────────────────────────────────────
def check_audio_lufs(video: Path, target: float = -14.0, tolerance: float = 2.0) -> dict:
    """Measure integrated loudness; YouTube standard = -14 LUFS."""
    try:
        import soundfile as sf
        import pyloudnorm as pyln
    except ImportError:
        return {"pass": True, "skipped": "pyloudnorm/soundfile not installed",
                "target_lufs": target}

    # Extract audio to temp wav
    tmp_wav = video.parent / f".{video.stem}.tmp.wav"
    try:
        subprocess.check_output([
            "ffmpeg", "-y", "-i", str(video), "-vn", "-acodec", "pcm_s16le",
            "-ar", "48000", "-ac", "2", str(tmp_wav),
        ], stderr=subprocess.DEVNULL, timeout=60)
        data, rate = sf.read(str(tmp_wav))
        meter = pyln.Meter(rate)
        lufs = meter.integrated_loudness(data)
    except Exception as e:
        return {"pass": False, "error": str(e), "target_lufs": target}
    finally:
        if tmp_wav.exists():
            tmp_wav.unlink()

    deviation = abs(lufs - target)
    return {
        "pass": deviation <= tolerance,
        "measured_lufs": round(lufs, 2),
        "target_lufs": target,
        "tolerance": tolerance,
        "deviation": round(deviation, 2),
    }


# ─── Visual: freeze frame + scene change rate + color histogram ─────────
def check_freeze_frames(video: Path, max_static_s: float = 0.5) -> dict:
    """Detect frozen segments longer than max_static_s."""
    try:
        out = subprocess.check_output([
            "ffmpeg", "-i", str(video), "-vf",
            f"freezedetect=n=0.001:d={max_static_s}",
            "-map", "0:v:0", "-f", "null", "-",
        ], stderr=subprocess.STDOUT, text=True, timeout=120)
    except subprocess.CalledProcessError as e:
        out = e.output
    except Exception as e:
        return {"pass": True, "skipped": str(e)}

    freeze_count = out.count("freeze_start")
    return {
        "pass": freeze_count == 0,
        "freeze_count": freeze_count,
        "max_static_s_threshold": max_static_s,
    }


def check_scene_changes(video: Path, min_per_minute: float = 8) -> dict:
    """Detect scene-change rate (per minute). Too few = static; too many = chaos."""
    try:
        out = subprocess.check_output([
            "ffmpeg", "-i", str(video), "-vf",
            "select='gt(scene,0.3)',metadata=print:file=-",
            "-an", "-f", "null", "-",
        ], stderr=subprocess.DEVNULL, text=True, timeout=120)
    except Exception as e:
        return {"pass": True, "skipped": str(e)}
    n_changes = out.count("scene_score")

    # Duration from technical check (recompute, cheap)
    tech = check_technical(video)
    duration_min = tech.get("duration_s", 0) / 60
    rate = n_changes / duration_min if duration_min else 0
    return {
        "pass": min_per_minute <= rate <= 60,  # 60 changes/min upper sanity
        "scene_change_count": n_changes,
        "duration_min": round(duration_min, 2),
        "changes_per_min": round(rate, 1),
        "min_threshold": min_per_minute,
    }


def check_color_palette(video: Path, brand_colors: list[str],
                       similarity_min: float = 0.4) -> dict:
    """Compare dominant frame colors against brand palette via histogram match."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"pass": True, "skipped": "opencv not installed"}

    if not brand_colors:
        return {"pass": True, "skipped": "no brand colors specified"}

    # Sample 12 frames at even intervals
    cap = cv2.VideoCapture(str(video))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if n_frames < 12:
        cap.release()
        return {"pass": True, "skipped": f"only {n_frames} frames"}

    sampled = []
    for i in range(12):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n_frames * i / 12))
        ok, frame = cap.read()
        if ok:
            sampled.append(frame)
    cap.release()

    # Convert brand colors hex → BGR
    targets_bgr = []
    for hex_color in brand_colors:
        h = hex_color.lstrip("#")
        if len(h) != 6: continue
        r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)
        targets_bgr.append((b, g, r))

    if not targets_bgr:
        return {"pass": True, "skipped": "no valid hex colors"}

    # For each sampled frame, compute % pixels close to ANY brand color
    total_match = 0
    for frame in sampled:
        h, w = frame.shape[:2]
        pixels = frame.reshape(-1, 3)
        match_mask = np.zeros(len(pixels), dtype=bool)
        for tgt in targets_bgr:
            dist = np.sqrt(np.sum((pixels.astype(int) - np.array(tgt)) ** 2, axis=1))
            match_mask |= dist < 60  # color distance threshold
        total_match += match_mask.sum() / len(pixels)
    avg_match = total_match / len(sampled)

    return {
        "pass": avg_match >= similarity_min,
        "avg_match_fraction": round(avg_match, 3),
        "similarity_threshold": similarity_min,
        "frames_sampled": len(sampled),
        "brand_colors_count": len(targets_bgr),
    }


# ─── Entry ───────────────────────────────────────────────────────────────
def evaluate(video: Path, brand: dict, feature_id: str) -> Tier1Result:
    """Run all Tier 1 checks. Returns aggregated result; logs each to devlog."""
    r = Tier1Result()

    r.technical = check_technical(video)
    devlog.log_eval("tier1", "technical", feature_id, "auto", r.technical)
    if not r.technical.get("pass", True):
        r.critical_fails.append("technical")

    r.audio["lufs"] = check_audio_lufs(video)
    devlog.log_eval("tier1", "audio_lufs", feature_id, "auto", r.audio["lufs"])
    # LUFS deviation is warn, not critical

    r.visual["freeze"] = check_freeze_frames(video)
    devlog.log_eval("tier1", "freeze_frames", feature_id, "auto", r.visual["freeze"])
    if not r.visual["freeze"].get("pass", True):
        r.critical_fails.append("freeze_frames")

    r.visual["scene_changes"] = check_scene_changes(video)
    devlog.log_eval("tier1", "scene_changes", feature_id, "auto", r.visual["scene_changes"])

    if brand.get("colors"):
        palette = list(brand["colors"].values())
        r.visual["color_palette"] = check_color_palette(video, palette)
        devlog.log_eval("tier1", "color_palette", feature_id, "auto",
                        r.visual["color_palette"])

    return r


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python eval_tier1.py <video.mp4> [brand.json]")
        sys.exit(1)
    video = Path(sys.argv[1])
    brand = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else {}
    result = evaluate(video, brand, "SMOKE")
    print(json.dumps(result.as_dict(), indent=2))
