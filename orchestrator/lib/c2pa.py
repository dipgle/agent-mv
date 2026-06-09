"""
C2PA Content Credentials — embeds AI-disclosure manifest into video/image files.

Why C2PA?
  - EU AI Act Article 50 requires AI-generated content to be machine-readable
    labeled. C2PA (Coalition for Content Provenance and Authenticity) is the
    industry-standard carrier for this requirement.
  - Meta, TikTok, and YouTube all accept C2PA manifests for AI-disclosure.
  - The c2pa-python library (Apache 2.0) embeds a signed or unsigned manifest
    into the mp4/png container without re-encoding the media.

Signing modes:
  - Signed (ED25519): set C2PA_SIGNING_KEY_PATH env var to a PEM private key.
    Verifiers can confirm the signer identity. Recommended for production.
  - Unsigned (Annotated Credentials): no env var needed. Manifest is present
    and machine-readable but not cryptographically verified. Fine for dev and
    for satisfying platform AI-labeling requirements.

Dev cert generation:
    python orchestrator/lib/c2pa.py gen-cert --out certs/dev

Then set:
    export C2PA_SIGNING_KEY_PATH=certs/dev/private.pem
    export C2PA_CERT_CHAIN_PATH=certs/dev/cert.pem

Usage:
    from orchestrator.lib import c2pa

    manifest = c2pa.build_manifest(feature_id="VID-001", model_stack=[...])
    c2pa.embed_credentials(Path("out/VID-001/final.mp4"), manifest)
    info = c2pa.verify(Path("out/VID-001/final.mp4"))

Requires: c2pa-python (Apache 2.0) — pip install c2pa-python
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import devlog

# ─── Version string baked into the manifest ──────────────────────────────

_PIPELINE_VERSION = "0.1.0"
_CLAIM_GENERATOR = f"agent-mv/{_PIPELINE_VERSION}"


# ─── Manifest builder ─────────────────────────────────────────────────────

def build_manifest(
    feature_id: str,
    model_stack: list[dict] | None = None,
    extra_assertions: list[dict] | None = None,
) -> dict:
    """
    Build a C2PA manifest dict ready to pass to embed_credentials().

    The manifest covers:
      - c2pa.actions  : created + aiGenerated
      - c2pa.training-mining : constrained (opt-out from AI training on output)
      - agent-mv.pipeline    : provenance — feature_id + model stack

    Args:
        feature_id:        Pipeline feature ID (e.g. "VID-001")
        model_stack:       List of dicts from infra/models.md parsing, each with
                           keys: role, model, license.  May be None.
        extra_assertions:  Additional C2PA assertion dicts to include.
    """
    actions = {
        "label": "c2pa.actions",
        "data": {
            "actions": [
                {
                    "action": "c2pa.created",
                    "softwareAgent": _CLAIM_GENERATOR,
                    "when": _now_iso(),
                },
                {
                    "action": "c2pa.aiGenerated",
                    "digitalSourceType": (
                        "https://cv.iptc.org/newscodes/digitalsourcetype/"
                        "trainedAlgorithmicMedia"
                    ),
                    "softwareAgent": _CLAIM_GENERATOR,
                },
            ]
        },
    }

    training_mining = {
        "label": "c2pa.training-mining",
        "data": {
            "entries": {
                "c2pa.ai_generative_training": {"use": "notAllowed"},
                "c2pa.ai_inference": {"use": "allowed"},
            }
        },
    }

    pipeline_assertion = {
        "label": "agent-mv.pipeline",
        "data": {
            "feature_id": feature_id,
            "pipeline_version": _PIPELINE_VERSION,
            "model_stack": model_stack or [],
            "claim_generator": _CLAIM_GENERATOR,
        },
    }

    assertions = [actions, training_mining, pipeline_assertion]
    if extra_assertions:
        assertions.extend(extra_assertions)

    return {
        "claim_generator": _CLAIM_GENERATOR,
        "assertions": assertions,
    }


# ─── Embed credentials into media file ────────────────────────────────────

def embed_credentials(video_path: Path, manifest: dict) -> bool:
    """
    Embed a C2PA Content Credentials manifest into video_path (mp4 or png).

    Modifies the file in-place (c2pa-python replaces the container metadata).

    Returns True if successful, False if c2pa-python is not installed (logs
    a warning event and continues gracefully).

    Signing:
      - If C2PA_SIGNING_KEY_PATH env var is set, sign with ED25519 key.
      - Otherwise, embed as unsigned Annotated Credentials.
    """
    try:
        import c2pa  # type: ignore   # pip install c2pa-python
    except ImportError:
        devlog.append("c2pa_skipped", "c2pa", "feature",
                      manifest.get("assertions", [{}])[-1].get("data", {}).get("feature_id", ""),
                      {
                          "reason": "c2pa-python not installed",
                          "hint": "pip install c2pa-python",
                          "video": str(video_path),
                      })
        return False

    if not video_path.exists():
        devlog.append("c2pa_error", "c2pa", "feature", "",
                      {"reason": "file not found", "video": str(video_path)})
        return False

    key_path = os.environ.get("C2PA_SIGNING_KEY_PATH", "")
    cert_path = os.environ.get("C2PA_CERT_CHAIN_PATH", "")

    manifest_json = json.dumps(manifest, ensure_ascii=False)

    try:
        if key_path and Path(key_path).exists() and cert_path and Path(cert_path).exists():
            # Signed mode: use ED25519 key + cert chain
            signer = c2pa.create_signer(
                Path(key_path).read_bytes(),
                Path(cert_path).read_bytes(),
                "es256",  # algorithm — c2pa-python supports es256 / ps256 / ed25519
            )
            builder = c2pa.Builder(manifest_json)
            builder.sign_file(signer, str(video_path), str(video_path))
            signed = True
        else:
            # Unsigned (Annotated Credentials) — manifest present but not signed
            builder = c2pa.Builder(manifest_json)
            builder.sign_file(None, str(video_path), str(video_path))
            signed = False

    except Exception as exc:
        devlog.append("c2pa_error", "c2pa", "feature",
                      manifest.get("assertions", [{}])[-1].get("data", {}).get("feature_id", ""),
                      {"error": str(exc), "video": str(video_path)})
        return False

    # Extract feature_id from the pipeline assertion for logging
    feature_id = _extract_feature_id(manifest)
    devlog.append("c2pa_embedded", "c2pa", "feature", feature_id, {
        "video": str(video_path),
        "signed": signed,
        "key_path": key_path or None,
        "claim_generator": manifest.get("claim_generator", _CLAIM_GENERATOR),
        "assertions_count": len(manifest.get("assertions", [])),
    })
    return True


# ─── Verify / read back manifest ─────────────────────────────────────────

def verify(video_path: Path) -> dict | None:
    """
    Read the C2PA manifest from video_path and return it as a dict.

    Returns None if c2pa-python is not installed or the file has no manifest.
    Useful for post-embed audit and for the /eval/api/c2pa/status endpoint.
    """
    try:
        import c2pa  # type: ignore
    except ImportError:
        return None

    if not video_path.exists():
        return None

    try:
        reader = c2pa.Reader.from_file(str(video_path))
        return json.loads(reader.json())
    except Exception as exc:
        return {"error": str(exc), "video": str(video_path)}


# ─── Dev certificate generation helper ────────────────────────────────────

def generate_dev_cert(out_dir: Path) -> bool:
    """
    Generate a self-signed ED25519 key pair suitable for dev/test C2PA signing.

    Creates:
      out_dir/private.pem  — ED25519 private key
      out_dir/cert.pem     — self-signed X.509 certificate (1 year)

    Requires openssl CLI on PATH.

    Usage:
        python orchestrator/lib/c2pa.py gen-cert --out certs/dev
    Then:
        export C2PA_SIGNING_KEY_PATH=certs/dev/private.pem
        export C2PA_CERT_CHAIN_PATH=certs/dev/cert.pem
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    key_pem = out_dir / "private.pem"
    cert_pem = out_dir / "cert.pem"

    try:
        # Generate ED25519 private key
        subprocess.check_call([
            "openssl", "genpkey", "-algorithm", "Ed25519",
            "-out", str(key_pem),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Generate self-signed cert (365 days)
        subprocess.check_call([
            "openssl", "req", "-new", "-x509",
            "-key", str(key_pem),
            "-out", str(cert_pem),
            "-days", "365",
            "-subj", "/CN=agent-mv-dev/O=DevOnly/C=XX",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print(f"Dev cert created: {cert_pem}")
        print(f"Private key:      {key_pem}")
        print()
        print("Set environment variables to enable signed C2PA:")
        print(f"  export C2PA_SIGNING_KEY_PATH={key_pem}")
        print(f"  export C2PA_CERT_CHAIN_PATH={cert_pem}")
        return True

    except subprocess.CalledProcessError as exc:
        print(f"openssl failed: {exc}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("openssl not found on PATH. Install OpenSSL first.", file=sys.stderr)
        return False


# ─── Helper functions ─────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_feature_id(manifest: dict) -> str:
    """Pull feature_id from the agent-mv.pipeline assertion if present."""
    for assertion in manifest.get("assertions", []):
        if assertion.get("label") == "agent-mv.pipeline":
            return assertion.get("data", {}).get("feature_id", "")
    return ""


# ─── CLI entry point ─────────────────────────────────────────────────────

def _cli():
    import argparse

    ap = argparse.ArgumentParser(description="C2PA Content Credentials helper")
    sub = ap.add_subparsers(dest="cmd")

    gen = sub.add_parser("gen-cert", help="Generate dev signing certificate")
    gen.add_argument("--out", default="certs/dev", help="Output directory")

    verify_p = sub.add_parser("verify", help="Read manifest from a media file")
    verify_p.add_argument("file", help="Path to mp4 or png")

    embed_p = sub.add_parser("embed", help="Embed manifest into a media file")
    embed_p.add_argument("file", help="Path to mp4 or png")
    embed_p.add_argument("--feature-id", default="MANUAL", help="Feature ID")

    args = ap.parse_args()

    if args.cmd == "gen-cert":
        ok = generate_dev_cert(Path(args.out))
        sys.exit(0 if ok else 1)

    elif args.cmd == "verify":
        result = verify(Path(args.file))
        if result is None:
            print("No C2PA manifest found (or c2pa-python not installed).")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.cmd == "embed":
        manifest = build_manifest(feature_id=args.feature_id)
        ok = embed_credentials(Path(args.file), manifest)
        print("Embedded." if ok else "Failed (check logs).")
        sys.exit(0 if ok else 1)

    else:
        ap.print_help()


if __name__ == "__main__":
    _cli()
