"""
LiteLLM client wrapper — calls the proxy on :4000 with auto-logging.

LiteLLM speaks OpenAI protocol, so we use the openai SDK with custom base_url.
"""

from __future__ import annotations
import os
import time
from typing import Any

from openai import OpenAI

from . import devlog

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_KEY = os.environ.get("LITELLM_MASTER_KEY", "sk-local-only-not-public")

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
) -> tuple[str, dict]:
    """
    Call LiteLLM-routed model. Returns (output_text, metadata).

    `model` is the LOGICAL name from infra/litellm.yaml (planner, executor, ...).
    """
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

    # LiteLLM injects cost via response metadata
    cost_usd = getattr(resp, "_hidden_params", {}).get("response_cost", 0.0)

    devlog.log_model_run(
        role=role,
        model=model,
        prompt=prompt,
        output_ref=text[:200],
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        modality="text",
        channel="api",
        feature_id=feature_id,
    )

    return text, {"latency_ms": latency_ms, "cost_usd": cost_usd}


def call_json(
    role: str,
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    feature_id: str = "",
    temperature: float = 0.2,
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
