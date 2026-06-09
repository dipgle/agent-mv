# C2PA Content Credentials

## Why

Several regulatory and platform requirements now mandate that AI-generated
content carry machine-readable provenance metadata:

- **EU AI Act Article 50** (in force 2026): AI-generated audio-visual content
  must be labeled in a machine-readable format.
- **Meta, TikTok, YouTube** all accept — and in some contexts require — C2PA
  manifests to surface AI-disclosure labels in their UIs.
- **C2PA** (Coalition for Content Provenance and Authenticity) is the
  cross-industry standard for this, backed by Adobe, Google, Microsoft, Sony,
  and others.

The `orchestrator/lib/c2pa.py` module in this pipeline embeds a manifest into
every produced `final.mp4` (and optionally keyframe PNGs) immediately after
ffmpeg compose.

## What gets embedded

Each manifest contains three assertions:

| Assertion | Content |
|-----------|---------|
| `c2pa.actions` | `c2pa.created` + `c2pa.aiGenerated` with `trainedAlgorithmicMedia` source type |
| `c2pa.training-mining` | `ai_generative_training: notAllowed` (opt-out from training data use) |
| `agent-mv.pipeline` | Custom: feature_id, pipeline version, model stack list |

The manifest's `claim_generator` field is set to `agent-mv/<version>`.

## Signing modes

| Mode | When | Effect |
|------|------|--------|
| **Signed** | `C2PA_SIGNING_KEY_PATH` env var set | Manifest is cryptographically bound to a key; verifiers can confirm signer identity |
| **Unsigned (Annotated Credentials)** | No key configured (default) | Manifest is present and machine-readable; no cryptographic guarantee on signer identity |

For most production use cases, Annotated Credentials satisfy platform
AI-labeling requirements.  Signed credentials are needed for supply-chain
integrity workflows.

## Generating a dev certificate

```bash
python orchestrator/lib/c2pa.py gen-cert --out certs/dev
```

This creates:
- `certs/dev/private.pem` — ED25519 private key
- `certs/dev/cert.pem`    — self-signed X.509 certificate (1 year)

Then enable signed mode:
```bash
export C2PA_SIGNING_KEY_PATH=certs/dev/private.pem
export C2PA_CERT_CHAIN_PATH=certs/dev/cert.pem
```

**Never commit private keys.**  Add `certs/` to `.gitignore`.

## Inspecting credentials on a produced video

### Using the official c2patool CLI

Install c2patool (Rust binary, free, Apache 2.0):
```bash
cargo install c2patool
# or download binary from https://github.com/contentauth/c2patool/releases
```

Inspect a video:
```bash
c2patool out/VID-001/final.mp4
```

Output is JSON showing the manifest store, assertions, and (if signed)
the certificate chain.

### Using the Python verify helper

```python
from orchestrator.lib.c2pa import verify
from pathlib import Path

info = verify(Path("out/VID-001/final.mp4"))
print(info)  # dict with full manifest store
```

### Via the eval dashboard

The **Compliance** tab at `http://localhost:7891/` shows C2PA embed status
per video (sourced from `kind=c2pa_embedded` events in devlog.sqlite).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `c2pa_skipped` events in devlog | `c2pa-python` not installed | `pip install c2pa-python` |
| `c2pa_error` events | Invalid key/cert format, or file locked | Check `C2PA_SIGNING_KEY_PATH` path; ensure file is not open |
| `verify()` returns `None` | File has no manifest or library missing | Check library install; confirm `embed_credentials` returned True |
| Platform rejects manifest | Unsigned credentials not accepted | Generate a signing cert (see above) and set env vars |

## References

- C2PA specification: https://c2pa.org/specifications/specifications/1.4/specs/C2PA_Specification.html
- c2pa-python library: https://github.com/contentauth/c2pa-python (Apache 2.0)
- c2patool CLI: https://github.com/contentauth/c2patool (Apache 2.0)
- EU AI Act text (Article 50): https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689
