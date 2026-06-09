"""
Content moderation — pre-publish legal compliance gate.

Runs deterministic checks on generated output BEFORE the Tier 2 LLM panel,
acting as a cheap early-rejection layer.

Checks:
  1. NSFW detection       — NudeNet (Apache 2.0) or fallback skip
  2. Real-person face     — OpenCV Haar cascade; consent check via brand.allow_real_faces
  3. Trademark similarity — CLIP nearest-neighbor against data/trademark_index/
  4. Voice clone consent  — brand.voice_reference.consent_form field present?

All deps are optional: missing library → check skipped (logged, not failed).

Usage:
    result = evaluate(final_path, sampled_frames, brand, feature_id)
    # result["aggregate"]["flagged"] == True  → moderation issues found
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import devlog

# Trademark index location — user drops PNGs here; pipeline auto-embeds on first run.
TRADEMARK_INDEX_DIR = Path("data/trademark_index")
TRADEMARK_EMBEDDINGS_NPZ = TRADEMARK_INDEX_DIR / "trademark_embeddings.npz"


# ─── Result dataclass ─────────────────────────────────────────────────────

@dataclass
class ModerationResult:
    """Outcome of a single moderation check."""
    flagged: bool = False
    # severity: "ok" | "major" | "critical"
    severity: str = "ok"
    categories: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "flagged": self.flagged,
            "severity": self.severity,
            "categories": self.categories,
            "details": self.details,
        }


def _ok(details: dict | None = None) -> ModerationResult:
    return ModerationResult(flagged=False, severity="ok", details=details or {})


def _major(categories: list[str], details: dict) -> ModerationResult:
    return ModerationResult(flagged=True, severity="major",
                            categories=categories, details=details)


def _critical(categories: list[str], details: dict) -> ModerationResult:
    return ModerationResult(flagged=True, severity="critical",
                            categories=categories, details=details)


# ─── Check 1: NSFW detection ─────────────────────────────────────────────

def check_nsfw(image_paths: list[Path]) -> ModerationResult:
    """
    Run NudeNet detector on every sampled frame.

    severity=critical if exposed private parts detected.
    severity=major    if suggestive / partial exposure detected.
    severity=ok       if nothing found or NudeNet not installed.

    NudeNet label reference (v3):
      Exposed: EXPOSED_ANUS, EXPOSED_GENITALIA_*, EXPOSED_BREAST_F, EXPOSED_BELLY (partial)
      Suggestive: COVERED_GENITALIA_*, COVERED_BREAST_*, EXPOSED_BUTTOCKS

    Requires: nudenet>=3.4.0 (Apache 2.0)
    """
    try:
        from nudenet import NudeDetector  # type: ignore
    except ImportError:
        return ModerationResult(
            flagged=False, severity="ok",
            details={"skipped": "nudenet not installed — pip install nudenet>=3.4.0"},
        )

    # Labels that indicate fully-exposed content → critical
    CRITICAL_LABELS = {
        "EXPOSED_ANUS", "EXPOSED_GENITALIA_F", "EXPOSED_GENITALIA_M",
        "EXPOSED_BREAST_F", "EXPOSED_NIPPLE",
    }
    # Labels indicating suggestive / partial exposure → major
    MAJOR_LABELS = {
        "COVERED_GENITALIA_F", "COVERED_GENITALIA_M",
        "COVERED_BREAST_F", "EXPOSED_BUTTOCKS", "EXPOSED_BELLY",
    }

    detector = NudeDetector()
    critical_hits: list[dict] = []
    major_hits: list[dict] = []

    for img_path in image_paths:
        if not img_path.exists():
            continue
        try:
            detections = detector.detect(str(img_path))
        except Exception as exc:
            continue  # single-frame failure; continue processing rest

        for det in detections:
            label = det.get("class", "")
            score = det.get("score", 0.0)
            if score < 0.5:
                continue  # below confidence threshold
            hit = {"frame": str(img_path), "label": label, "score": round(score, 3)}
            if label in CRITICAL_LABELS:
                critical_hits.append(hit)
            elif label in MAJOR_LABELS:
                major_hits.append(hit)

    if critical_hits:
        return _critical(
            categories=["nsfw_exposed"],
            details={"hits": critical_hits, "count": len(critical_hits)},
        )
    if major_hits:
        return _major(
            categories=["nsfw_suggestive"],
            details={"hits": major_hits, "count": len(major_hits)},
        )
    return _ok({"frames_checked": len(image_paths)})


# ─── Check 2: Real-person face detection ──────────────────────────────────

def check_real_person(image_paths: list[Path], brand: dict) -> ModerationResult:
    """
    Detect human faces via OpenCV Haar cascade.

    If brand.allow_real_faces == true → skip (operator confirmed consent).
    Otherwise: face detected → severity=major with advisory message.

    Requires: opencv-python (already in requirements.txt)
    """
    # Brand opt-out: operator confirms all faces have consent
    if brand.get("allow_real_faces", False):
        return _ok({"skipped": "brand.allow_real_faces=true — consent assumed"})

    try:
        import cv2  # type: ignore
    except ImportError:
        return ModerationResult(
            flagged=False, severity="ok",
            details={"skipped": "opencv not installed"},
        )

    # Use the standard frontal-face Haar cascade bundled with OpenCV
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)

    total_faces = 0
    face_frames: list[str] = []

    for img_path in image_paths:
        if not img_path.exists():
            continue
        try:
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
            )
            n = len(faces) if hasattr(faces, "__len__") else 0
            if n > 0:
                total_faces += n
                face_frames.append(str(img_path))
        except Exception:
            continue

    if total_faces > 0:
        return _major(
            categories=["real_person_face"],
            details={
                "face_count": total_faces,
                "frames_with_faces": face_frames,
                "message": (
                    "Real-person face detected; verify consent before publishing. "
                    "Set brand.allow_real_faces=true to suppress this warning."
                ),
            },
        )
    return _ok({"frames_checked": len(image_paths), "faces_found": 0})


# ─── Check 3: Trademark / logo similarity ─────────────────────────────────

def _build_trademark_embeddings(model: Any, preprocess: Any) -> tuple[Any, list[str]] | None:
    """
    Embed all PNG files in data/trademark_index/ and cache to .npz.
    Returns (embeddings_array, names_list) or None if index is empty.
    """
    import numpy as np  # type: ignore
    import torch  # type: ignore
    from PIL import Image  # type: ignore

    logos = list(TRADEMARK_INDEX_DIR.glob("*.png")) + list(TRADEMARK_INDEX_DIR.glob("*.jpg"))
    if not logos:
        return None

    tensors = []
    names = []
    for p in logos:
        try:
            img = preprocess(Image.open(p).convert("RGB")).unsqueeze(0)
            tensors.append(img)
            names.append(p.name)
        except Exception:
            continue

    if not tensors:
        return None

    batch = torch.cat(tensors, dim=0)
    with torch.no_grad():
        embeddings = model.encode_image(batch)
        embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)

    arr = embeddings.cpu().numpy()
    np.savez(TRADEMARK_EMBEDDINGS_NPZ, embeddings=arr, names=names)
    return arr, names


def check_trademark_similarity(image_paths: list[Path],
                               threshold: float = 0.85) -> ModerationResult:
    """
    CLIP-based nearest-neighbor similarity against data/trademark_index/.

    User drops PNG/JPG trademark images into data/trademark_index/.
    First run builds trademark_embeddings.npz; subsequent runs load cache.
    If the index is empty → check is skipped (not flagged).

    Cosine similarity > threshold → flagged as potential trademark match.

    Requires: open-clip-torch>=2.24.0 (MIT) + torch
    """
    if not TRADEMARK_INDEX_DIR.exists() or not any(TRADEMARK_INDEX_DIR.glob("*.png")):
        return _ok({"skipped": "trademark_index empty — add PNGs to data/trademark_index/"})

    try:
        import open_clip  # type: ignore
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return ModerationResult(
            flagged=False, severity="ok",
            details={
                "skipped": (
                    "open-clip-torch not installed — "
                    "pip install open-clip-torch>=2.24.0"
                )
            },
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Load a small, fast CLIP model suitable for similarity checks
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="openai"
    )
    model = model.to(device).eval()

    # Build / load trademark embeddings
    if TRADEMARK_EMBEDDINGS_NPZ.exists():
        data = np.load(str(TRADEMARK_EMBEDDINGS_NPZ), allow_pickle=True)
        tm_embeddings = data["embeddings"]
        tm_names = list(data["names"])
    else:
        result = _build_trademark_embeddings(model, preprocess)
        if result is None:
            return _ok({"skipped": "trademark_index contained no embeddable images"})
        tm_embeddings, tm_names = result

    hits: list[dict] = []

    for img_path in image_paths:
        if not img_path.exists():
            continue
        try:
            img_tensor = preprocess(
                Image.open(img_path).convert("RGB")
            ).unsqueeze(0).to(device)
            with torch.no_grad():
                emb = model.encode_image(img_tensor)
                emb = emb / emb.norm(dim=-1, keepdim=True)
            emb_np = emb.cpu().numpy()  # shape (1, D)

            # Cosine similarity against all trademark embeddings
            sims = (tm_embeddings @ emb_np.T).squeeze()  # shape (N,)
            if sims.ndim == 0:
                sims = sims.reshape(1)

            max_idx = int(np.argmax(sims))
            max_sim = float(sims[max_idx])

            if max_sim >= threshold:
                hits.append({
                    "frame": str(img_path),
                    "trademark": tm_names[max_idx],
                    "cosine_similarity": round(max_sim, 4),
                })
        except Exception:
            continue

    if hits:
        return _major(
            categories=["trademark_similarity"],
            details={
                "hits": hits,
                "threshold": threshold,
                "message": (
                    f"Frame(s) visually similar to registered trademark images "
                    f"(cosine > {threshold}). Verify legal clearance."
                ),
            },
        )
    return _ok({
        "frames_checked": len(image_paths),
        "trademark_count": len(tm_names),
        "threshold": threshold,
    })


# ─── Check 4: Voice clone consent ─────────────────────────────────────────

def check_audio_voice_clone_consent(brand: dict) -> ModerationResult:
    """
    Verify voice reference consent field is explicitly set.

    If brand.voice_reference is present (i.e., a voice clone is being used),
    brand.voice_reference.consent_form must be truthy — otherwise severity=major.

    If no voice_reference at all → default TTS, no consent issue.
    """
    voice_ref = brand.get("voice_reference")
    if not voice_ref:
        # No voice clone being used — no issue
        return _ok({"skipped": "no voice_reference in brand — using default TTS"})

    consent = voice_ref.get("consent_form")
    if not consent:
        return _major(
            categories=["voice_clone_consent_missing"],
            details={
                "message": (
                    "brand.voice_reference is set but consent_form field is missing "
                    "or false. Add brand.voice_reference.consent_form = true (or a "
                    "URL to the signed consent document) before publishing a voice clone."
                )
            },
        )

    return _ok({
        "voice_reference_path": voice_ref.get("wav_path", ""),
        "consent_form": str(consent),
    })


# ─── Aggregate evaluator ─────────────────────────────────────────────────

def evaluate(
    final_path: Path,
    sampled_frames: list[Path],
    brand: dict,
    feature_id: str,
) -> dict:
    """
    Run all four moderation checks; log each as kind='moderation' in devlog.
    Returns aggregate dict with per-check results and top-level flags.

    Intended to run BEFORE the Tier 2 LLM panel (cheap deterministic gate).

    Returns:
        {
          "nsfw":          ModerationResult.as_dict(),
          "real_person":   ModerationResult.as_dict(),
          "trademark":     ModerationResult.as_dict(),
          "voice_consent": ModerationResult.as_dict(),
          "aggregate": {
              "flagged": bool,
              "has_critical": bool,
              "has_major": bool,
              "critical_checks": [...],
              "major_checks": [...],
          }
        }
    """
    results: dict[str, ModerationResult] = {}

    results["nsfw"] = check_nsfw(sampled_frames)
    devlog.append("moderation", "auto", "feature", feature_id, {
        "check": "nsfw",
        **results["nsfw"].as_dict(),
    })

    results["real_person"] = check_real_person(sampled_frames, brand)
    devlog.append("moderation", "auto", "feature", feature_id, {
        "check": "real_person",
        **results["real_person"].as_dict(),
    })

    results["trademark"] = check_trademark_similarity(sampled_frames)
    devlog.append("moderation", "auto", "feature", feature_id, {
        "check": "trademark",
        **results["trademark"].as_dict(),
    })

    results["voice_consent"] = check_audio_voice_clone_consent(brand)
    devlog.append("moderation", "auto", "feature", feature_id, {
        "check": "voice_consent",
        **results["voice_consent"].as_dict(),
    })

    critical_checks = [k for k, v in results.items() if v.severity == "critical"]
    major_checks = [k for k, v in results.items() if v.severity == "major"]
    has_critical = bool(critical_checks)
    has_major = bool(major_checks)

    aggregate = {
        "flagged": has_critical or has_major,
        "has_critical": has_critical,
        "has_major": has_major,
        "critical_checks": critical_checks,
        "major_checks": major_checks,
    }

    return {
        "nsfw": results["nsfw"].as_dict(),
        "real_person": results["real_person"].as_dict(),
        "trademark": results["trademark"].as_dict(),
        "voice_consent": results["voice_consent"].as_dict(),
        "aggregate": aggregate,
    }


def sample_frames_from_video(video_path: Path, n: int = 6) -> list[Path]:
    """
    Extract n evenly-spaced frames from a video for moderation checks.
    Writes to a temp directory alongside the video.
    Returns list of PNG paths (may be empty on failure).
    """
    import subprocess
    import tempfile

    out_dir = video_path.parent / ".moderation_frames"
    out_dir.mkdir(exist_ok=True)

    # Use ffmpeg to extract frames at even intervals
    frames: list[Path] = []
    try:
        # Get duration first
        probe = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-print_format", "json", str(video_path),
        ], text=True, timeout=15)
        duration = float(json.loads(probe)["format"].get("duration", 0))
        if duration <= 0:
            return []

        interval = duration / (n + 1)
        for i in range(1, n + 1):
            ts = i * interval
            out_frame = out_dir / f"frame_{i:02d}.png"
            subprocess.check_call([
                "ffmpeg", "-y", "-ss", str(ts), "-i", str(video_path),
                "-frames:v", "1", str(out_frame),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            if out_frame.exists():
                frames.append(out_frame)
    except Exception:
        pass

    return frames
