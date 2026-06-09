"""
LiteLLM client wrapper — calls the proxy on :4000 with auto-logging + cost gate.

LiteLLM speaks OpenAI protocol, so we use the openai SDK with custom base_url.

Cost tracking:
  - Provider-reported cost extracted from LiteLLM response (when available)
  - Falls back to token-based estimate via lib.cost
  - Adds compute/electricity for local hardware
"""

from __future__ import annotations
import os
import time
from typing import Any

from openai import OpenAI

from . import devlog, cost, cost_gate

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
# Default kept intentionally non-sk-prefixed so we don't trigger cred-scanners.
# Override via env LITELLM_MASTER_KEY in .env.
LITELLM_KEY = os.environ.get("LITELLM_MASTER_KEY", "local-dev-noauth")

_client = OpenAI(base_url=LITELLM_BASE, api_key=LITELLM_KEY)


def call(
    role: str,
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    feature_id: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    response_format: dict | None = None,
    skip_gate: bool = False,
    est_cost: float = 0.0,
) -> tuple[str, dict]:
    """
    Call LiteLLM-routed model. Returns (output_text, metadata).

    `model` is the LOGICAL name from infra/litellm.yaml (planner, executor, ...).

    Cost gate kicks in unless skip_gate=True: budget overrun triggers
    cascade fallback to cheaper model (logged via decision event).
    """
    # ─── Cost gate (may downgrade model) ─────────────────────────────────
    if not skip_gate and feature_id:
        gated = cost_gate.gate(feature_id, model, est_cost=est_cost)
        if gated != model:
            model = gated  # cost_gate already logged the decision

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    t0 = time.time()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    resp = _client.chat.completions.create(**kwargs)
    latency_ms = int((time.time() - t0) * 1000)

    text = resp.choices[0].message.content or ""

    # ─── Extract cost: prefer provider-reported, fall back to estimate ────
    provider_cost = None
    hidden = getattr(resp, "_hidden_params", None)
    if hidden:
        provider_cost = hidden.get("response_cost")

    usage = getattr(resp, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    cached_tokens = 0
    if usage and hasattr(usage, "prompt_tokens_details"):
        cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0

    cost_bd = cost.estimate(
        model=model,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_tokens,
        cloud_reported_cost=provider_cost,
    )

    devlog.log_model_run(
        role=role,
        model=model,
        prompt=prompt,
        output_ref=text[:200],
        latency_ms=latency_ms,
        cost=cost_bd.as_dict(),
        modality="text",
        channel="api",
        feature_id=feature_id,
        metrics={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
        },
    )

    return text, {
        "latency_ms": latency_ms,
        "cost": cost_bd.as_dict(),
        "model_used": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def call_json(
    role: str,
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    feature_id: str = "",
    temperature: float = 0.2,
    skip_gate: bool = False,
    est_cost: float = 0.0,
) -> tuple[dict, dict]:
    """Call with JSON response format constraint."""
    import json
    text, meta = call(
        role=role,
        model=model,
        prompt=prompt,
        system=system,
        feature_id=feature_id,
        temperature=temperature,
        response_format={"type": "json_object"},
        skip_gate=skip_gate,
        est_cost=est_cost,
    )
    try:
        return json.loads(text), meta
    except json.JSONDecodeError:
        # Fallback: try to extract JSON block
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0)), meta
        raise
