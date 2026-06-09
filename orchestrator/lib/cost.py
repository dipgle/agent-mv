"""
Cost estimation — every model call attaches accurate $ cost.

3 cost sources:
  1. Cloud API   — provider's billed cost (Anthropic, OpenAI, Groq, ...)
  2. GPU compute — local owned (electricity only) or rented ($/hour)
  3. Electricity — for owned GPU (watts * hours * $/kWh)

Read by litellm_client + comfy_client to set `cost_usd` on every model_run event.
Read by supervisor/cost_rollup for daily/weekly aggregation.
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional

# ─── Hardware catalog ─────────────────────────────────────────────────────
# Owned hardware has 0 $/hour (capital already sunk); add electricity below.
# Rented hardware uses on-demand or spot rates.
HARDWARE_USD_HOUR: dict[str, float] = {
    "M3_Max_owned":      0.0,
    "M4_Max_owned":      0.0,
    "RTX_3090_owned":    0.0,
    "RTX_4090_owned":    0.0,
    "RTX_5090_owned":    0.0,
    # Rented (typical 2026 rates — update via supervisor scan)
    "RTX_4090_runpod":   0.40,
    "RTX_4090_vast_od":  0.35,
    "RTX_4090_vast_spot": 0.18,
    "A100_40GB_runpod":  1.20,
    "A100_80GB_runpod":  1.90,
    "H100_80GB_runpod":  2.80,
    "L40S_runpod":       1.00,
}

# Watts at peak load (for owned hardware electricity calc)
HARDWARE_WATTS: dict[str, int] = {
    "M3_Max_owned": 80,
    "M4_Max_owned": 80,
    "RTX_3090_owned": 350,
    "RTX_4090_owned": 450,
    "RTX_5090_owned": 575,
}

ELECTRICITY_USD_KWH = float(os.environ.get("ELECTRICITY_USD_KWH", "0.12"))
DEFAULT_HARDWARE = os.environ.get("PIPELINE_HARDWARE", "M3_Max_owned")


# ─── Cloud API pricing (per 1M tokens unless noted) ───────────────────────
# Supervisor scan keeps this in sync; manual fallback if scan stale.
# Updated 2026-06-09 via Anthropic/OpenAI/Google/Groq pricing pages.
CLOUD_PRICING: dict[str, dict] = {
    # Anthropic
    "claude-opus-4-7":   {"input": 15.00, "output": 75.00, "cache_read": 1.50},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_read": 0.30},
    "claude-haiku-4-5":  {"input": 0.80,  "output": 4.00,  "cache_read": 0.08},
    # OpenAI
    "gpt-5":             {"input": 10.00, "output": 40.00},
    "gpt-5-codex":       {"input": 5.00,  "output": 20.00,
                          "pool_note": "trial credits or ChatGPT Plus quota — "
                                       "rotated via LiteLLM key pool; effective "
                                       "cost depends on which key is active"},
    "gpt-5-mini":        {"input": 0.25,  "output": 1.00},
    # Mistral free tier (Codestral, code-specific)
    "codestral-latest":  {"input": 0.0,   "output": 0.0,
                          "free_quota": "rate-limited free tier"},
    # Google
    "gemini-3-pro":      {"input": 1.25,  "output": 10.00},
    "gemini-3-flash":    {"input": 0.075, "output": 0.30},
    # Free tier APIs (logged at $0 but track quota usage separately)
    "groq-llama-3.3-70b":     {"input": 0.0, "output": 0.0, "free_quota": "14000/day"},
    "cerebras-llama-3.3-70b": {"input": 0.0, "output": 0.0, "free_quota": "8000/day"},
    # Visual gen API (per-clip pricing, not token)
    "runway-gen-3":      {"per_clip_5s": 0.95},
    "runway-gen-3-turbo":{"per_clip_5s": 0.50},
    "pika-2-0":          {"per_clip_4s": 0.45},
    "elevenlabs-tts":    {"per_1k_chars": 0.30},
    "suno-v4":           {"per_song": 0.10},
}


@dataclass
class CostBreakdown:
    cloud_usd: float = 0.0
    compute_usd: float = 0.0     # rented GPU $/hr * hours
    electricity_usd: float = 0.0  # owned GPU watts * hours * $/kWh
    total_usd: float = 0.0
    hardware: str = ""
    latency_ms: int = 0
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "cloud_usd": round(self.cloud_usd, 6),
            "compute_usd": round(self.compute_usd, 6),
            "electricity_usd": round(self.electricity_usd, 6),
            "total_usd": round(self.total_usd, 6),
            "hardware": self.hardware,
            "latency_ms": self.latency_ms,
            "note": self.note,
        }


def estimate(
    *,
    model: str,
    latency_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    hardware: str | None = None,
    n_units: int = 1,
    cloud_reported_cost: float | None = None,
) -> CostBreakdown:
    """
    Compute cost for a single model call.

    Args:
        model:       canonical model id (e.g. "anthropic/claude-sonnet-4-6")
        latency_ms:  wall time the call took
        input_tokens / output_tokens / cached_input_tokens: for token-priced models
        hardware:    physical hardware running the call (default $PIPELINE_HARDWARE)
        n_units:     unit count for per-unit pricing (e.g. n clips for Runway)
        cloud_reported_cost: if LiteLLM/provider returns exact cost, use that and skip token math

    Returns:
        CostBreakdown
    """
    hardware = hardware or DEFAULT_HARDWARE
    bd = CostBreakdown(hardware=hardware, latency_ms=latency_ms)

    # Strip provider prefix to look up pricing
    canonical = model.split("/")[-1] if "/" in model else model

    # ─── Cloud cost ──────────────────────────────────────────────────
    if cloud_reported_cost is not None:
        bd.cloud_usd = cloud_reported_cost
        bd.note = "provider-reported"
    elif canonical in CLOUD_PRICING:
        p = CLOUD_PRICING[canonical]
        if "per_clip_5s" in p:
            bd.cloud_usd = p["per_clip_5s"] * n_units
        elif "per_clip_4s" in p:
            bd.cloud_usd = p["per_clip_4s"] * n_units
        elif "per_song" in p:
            bd.cloud_usd = p["per_song"] * n_units
        elif "per_1k_chars" in p:
            bd.cloud_usd = p["per_1k_chars"] * n_units / 1000
        elif "input" in p:
            # Per-token pricing
            cache_read_cost = p.get("cache_read", p["input"]) * cached_input_tokens / 1_000_000
            input_cost = p["input"] * (input_tokens - cached_input_tokens) / 1_000_000
            output_cost = p["output"] * output_tokens / 1_000_000
            bd.cloud_usd = cache_read_cost + input_cost + output_cost

    # ─── Compute / electricity cost ──────────────────────────────────
    hours = latency_ms / 1000.0 / 3600.0
    if "owned" in hardware:
        watts = HARDWARE_WATTS.get(hardware, 100)
        bd.electricity_usd = hours * watts / 1000.0 * ELECTRICITY_USD_KWH
    elif hardware in HARDWARE_USD_HOUR:
        bd.compute_usd = hours * HARDWARE_USD_HOUR[hardware]

    bd.total_usd = bd.cloud_usd + bd.compute_usd + bd.electricity_usd
    return bd


def tier_of(model: str) -> str:
    """Classify model for cost/quality bucketing — same convention as devlog.tier_of."""
    m = model.lower()
    if "ollama/" in m or "comfy/" in m or "local/" in m: return "B"
    if any(x in m for x in ("groq/", "cerebras/", ":free")): return "A-"
    if "claude-haiku" in m or "gemini-flash" in m or "gpt-5-mini" in m: return "A-"
    if "claude-sonnet" in m or "gpt-5-codex" in m or "gemini-pro" in m: return "A"
    if "claude-opus" in m or "gpt-5" in m or "gemini-3-pro" in m: return "S"
    if any(x in m for x in ("runway/", "pika/", "sora", "elevenlabs", "suno")): return "S"
    return "?"


def is_free(model: str) -> bool:
    canonical = model.split("/")[-1] if "/" in model else model
    p = CLOUD_PRICING.get(canonical, {})
    return p.get("free_quota") is not None


def has_pricing(model: str) -> bool:
    canonical = model.split("/")[-1] if "/" in model else model
    return canonical in CLOUD_PRICING


if __name__ == "__main__":
    # Smoke test
    bd = estimate(
        model="anthropic/claude-sonnet-4-6",
        latency_ms=3500,
        input_tokens=4000,
        output_tokens=600,
        cached_input_tokens=2000,
    )
    print("Sonnet 4.6 call:", bd.as_dict())

    bd = estimate(model="ollama/qwen3-coder:30b", latency_ms=12000)
    print("Local Qwen 30b 12s:", bd.as_dict())

    bd = estimate(model="runway/runway-gen-3", latency_ms=180000, n_units=1)
    print("Runway 1 clip:", bd.as_dict())
