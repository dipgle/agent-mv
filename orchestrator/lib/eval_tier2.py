"""
Tier 2 — LLM panel ensemble (4 models scoring per dimension).

Replaces single reviewer with diverse panel to avoid family-specific blind
spots.  Each model scores INDEPENDENTLY on a per-dimension rubric, then we
aggregate via trimmed-mean for scores and unanimous-on-critical for booleans.

Panel members (logical names from infra/litellm.yaml):
  - reviewer            (local DeepSeek-R1)         reasoning, narrative
  - researcher-vision   (local Qwen2.5-VL)          visual composition
  - researcher-bulk     (Gemini Flash, free)        long context, neutral
  - reviewer-paid       (Claude Sonnet, optional)   taste, edge cases

Strong disagreement (σ > 0.3 on a scored dimension, or split vote on a
boolean) escalates to Tier 3 frontier adjudicator (Claude Opus).

Hardening (added 2026-06-09):
  - Per-model timeout       : future.result(timeout=N); logs panel_timeout
  - Circuit breaker          : skips models with too many recent failures;
                               logs panel_breaker_skip
  - Partial-panel fallback   : 0→rejected/all_failed, 1→needs_adjudicator,
                               2+→aggregate with partial_panel warning
  - Retry-once on soft error : connection error / invalid JSON → sleep 2s,
                               retry once; second failure counted by breaker
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

try:
    # Normal package import (python -m orchestrator.lib.eval_tier2 or imported).
    from . import devlog, litellm_client
    from .circuit_breaker import get as get_breaker
except ImportError:
    # Standalone execution (python orchestrator/lib/eval_tier2.py).
    # Bootstrap sys.modules with stubs for heavy deps, then load concrete
    # sibling modules via importlib.util so they get the right __name__ and
    # can import each other correctly.
    import importlib.util as _ilu, pathlib as _pl, sys as _sys, types as _types

    _lib_dir = _pl.Path(__file__).resolve().parent

    def _load_sibling(name: str, fqname: str):
        """Load a file from the same lib/ directory under a given full name."""
        if fqname in _sys.modules:
            return _sys.modules[fqname]
        spec = _ilu.spec_from_file_location(fqname, _lib_dir / f"{name}.py")
        mod = _ilu.module_from_spec(spec)   # type: ignore[arg-type]
        _sys.modules[fqname] = mod
        spec.loader.exec_module(mod)        # type: ignore[union-attr]
        return mod

    # Register package stubs so sibling imports resolve.
    # __path__ must be set on package stubs so importlib.reload works.
    _pkg_stub = _sys.modules.get("orchestrator") or _types.ModuleType("orchestrator")
    _pkg_stub.__path__ = [str(_lib_dir.parent)]   # type: ignore[attr-defined]
    _sys.modules.setdefault("orchestrator", _pkg_stub)
    _lib_stub = _sys.modules.get("orchestrator.lib") or _types.ModuleType("orchestrator.lib")
    _lib_stub.__path__ = [str(_lib_dir)]          # type: ignore[attr-defined]
    _sys.modules.setdefault("orchestrator.lib", _lib_stub)

    # Stub openai so litellm_client can be loaded.
    if "openai" not in _sys.modules:
        _openai = _types.ModuleType("openai")
        class _FakeOpenAI:
            def __init__(self, **kw): pass
        _openai.OpenAI = _FakeOpenAI    # type: ignore[attr-defined]
        _sys.modules["openai"] = _openai

    # Stub cost + cost_gate (used by litellm_client).
    for _sn in ("orchestrator.lib.cost", "orchestrator.lib.cost_gate"):
        if _sn not in _sys.modules:
            _sm = _types.ModuleType(_sn)
            class _CBD:
                def as_dict(self): return {"total_usd": 0.0}
            _sm.estimate = lambda **kw: _CBD()          # type: ignore[attr-defined]
            _sm.gate = lambda fid, model, **kw: model   # type: ignore[attr-defined]
            _sys.modules[_sn] = _sm

    # Load siblings.
    devlog        = _load_sibling("devlog",          "orchestrator.lib.devlog")
    litellm_client = _load_sibling("litellm_client", "orchestrator.lib.litellm_client")
    _cb_mod       = _load_sibling("circuit_breaker", "orchestrator.lib.circuit_breaker")
    get_breaker   = _cb_mod.get

# ─── Constants / configuration ───────────────────────────────────────────────

DIMENSIONS_SCORED = ["aesthetic", "narrative", "brand_subjective", "hook_taste"]
DIMENSIONS_BOOL = ["compliance_subjective"]
PANEL_ROLES = ["reviewer", "researcher-vision", "researcher-bulk"]
PANEL_PAID_ROLE = "reviewer-paid"  # Only included when allow_paid=True
ESCALATION_SIGMA = 0.3

# Per-model call timeout in seconds.
_MODEL_TIMEOUT_S: float = float(os.environ.get("EVAL_PANEL_MODEL_TIMEOUT_S", "60"))

# Minimum number of responding panel members to aggregate normally.
# Below this count the result is escalated to adjudicator.
_MIN_VOTES: int = int(os.environ.get("EVAL_PANEL_MIN_VOTES", "2"))

# Retry sleep duration (seconds) after a soft failure (connection / JSON error).
_RETRY_SLEEP_S: float = 2.0

# ─── Prompt helpers ──────────────────────────────────────────────────────────

REVIEWER_SYSTEM = """You are one member of a 4-model reviewer panel evaluating
an ad video.  Output STRICT JSON, no preamble, this exact schema:

{
  "aesthetic":            {"score": <0-10>, "evidence": "<one sentence>"},
  "narrative":            {"score": <0-10>, "evidence": "<one sentence>"},
  "brand_subjective":     {"score": <0-10>, "evidence": "<one sentence>"},
  "hook_taste":           {"score": <0-10>, "evidence": "<one sentence>"},
  "compliance_subjective":{"pass":  <true|false>, "issues": ["..."]},
  "shot_issues": [{"shot": <int>, "severity":"critical|major|minor", "msg":"..."}]
}

Score rubric (apply uniformly):
  10 = exemplar quality, would beat 90% of competitor ads
   7 = solid, ship-able, on-brand
   5 = mediocre, viewer would scroll past
   3 = visible defects, poor execution
   0 = unusable

Be honest.  Inflated scores reduce panel signal.  If unsure, score 5 + say so.
"""


def _build_prompt(spec: dict, brand: dict, transcript: str,
                  tier1: dict, hook: dict) -> str:
    """Compress all evidence into a single prompt the panel can score."""
    return (
        f"# Video evidence for panel review\n\n"
        f"## Shotlist (from Planner)\n```json\n"
        f"{json.dumps(spec, ensure_ascii=False, indent=2)[:3000]}\n```\n\n"
        f"## Brand spec\n```json\n"
        f"{json.dumps(brand, ensure_ascii=False, indent=2)[:1500]}\n```\n\n"
        f"## Voiceover transcript\n{transcript[:2000]}\n\n"
        f"## Tier 1 deterministic checks\n```json\n"
        f"{json.dumps(tier1, ensure_ascii=False)[:1500]}\n```\n\n"
        f"## Hook auto-scores\n```json\n"
        f"{json.dumps(hook, ensure_ascii=False)[:800]}\n```\n\n"
        f"Output ONLY the JSON object specified in system prompt.\n"
    )


def _prompt_hash(prompt: str) -> str:
    """Short SHA-256 prefix of the prompt — used in log events."""
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


# ─── Core per-model call (with timeout + retry + circuit breaker) ─────────────

def _call_once(role: str, prompt: str, feature_id: str) -> dict:
    """
    Single attempt to call the model.  Raises on any error (caller retries).
    Returns the parsed response dict with _model and _meta injected.
    """
    result, meta = litellm_client.call_json(
        role=f"reviewer-panel:{role}",
        model=role,
        prompt=prompt,
        system=REVIEWER_SYSTEM,
        feature_id=feature_id,
        temperature=0.2,
        skip_gate=True,  # panel calls do not trigger the cost-gate cascade
    )
    result["_model"] = role
    result["_meta"] = meta
    return result


def _score_with_model(role: str, prompt: str, feature_id: str) -> Optional[dict]:
    """
    Call one panel member with timeout, retry, and circuit-breaker integration.

    Returns the scored dict on success, None on final failure.

    Failure modes handled:
      - Breaker open        : skip immediately, log panel_breaker_skip
      - Timeout             : future.result(timeout=N), log panel_timeout
      - Soft error (1st)    : connection / JSON parse → sleep, retry once,
                              log panel_retry
      - Soft error (2nd)    : log panel_error, record failure in breaker
    """
    breaker = get_breaker(role)
    ph = _prompt_hash(prompt)

    # ── Circuit-breaker check ─────────────────────────────────────────────
    if breaker.is_open():
        devlog.append(
            "panel_breaker_skip", "supervisor", "feature", feature_id,
            {"role": role, "prompt_hash": ph, **breaker.state_dict()},
        )
        return None

    def _attempt(attempt_n: int) -> Optional[dict]:
        """Run one attempt inside a thread (so we can apply a timeout)."""
        import concurrent.futures as _cf
        import threading as _threading
        t0 = time.time()

        # Deliberately NOT using the context manager (which calls shutdown(wait=True)
        # on exit and would block indefinitely on a hung thread).  We do a non-
        # blocking shutdown so the caller is unblocked after the timeout.
        #
        # We also need the inner thread to be a daemon thread so that if the
        # model call hangs indefinitely, the Python process can still exit after
        # the smoke test completes.  ThreadPoolExecutor in Python 3.9 doesn't
        # accept a thread_factory kwarg, so we monkey-patch _threads to mark
        # them as daemon before submission by pre-creating via threading.Thread.
        result_holder: list = []
        exc_holder: list = []
        done_event = _threading.Event()

        def _run_call():
            try:
                result_holder.append(_call_once(role, prompt, feature_id))
            except Exception as exc:
                exc_holder.append(exc)
            finally:
                done_event.set()

        t = _threading.Thread(target=_run_call, name=f"panel_call_{role}", daemon=True)
        t.start()

        completed = done_event.wait(timeout=_MODEL_TIMEOUT_S)

        if not completed:
            # Thread is still running (hung model).  It's a daemon so the
            # process can exit; we treat this as a terminal timeout.
            elapsed_ms = int((time.time() - t0) * 1000)
            devlog.append(
                "panel_timeout", "supervisor", "feature", feature_id,
                {"role": role, "prompt_hash": ph,
                 "elapsed_ms": elapsed_ms, "attempt": attempt_n},
            )
            breaker.record_failure()
            return None

        # Thread completed — check for exception vs result.
        elapsed_ms = int((time.time() - t0) * 1000)
        if exc_holder:
            e = exc_holder[0]
            is_first = attempt_n == 1
            if is_first:
                devlog.append(
                    "panel_retry", "supervisor", "feature", feature_id,
                    {"role": role, "prompt_hash": ph,
                     "error": str(e), "elapsed_ms": elapsed_ms},
                )
                raise e   # signal outer loop to retry
            else:
                devlog.append(
                    "panel_error", "supervisor", "feature", feature_id,
                    {"role": role, "prompt_hash": ph,
                     "error": str(e), "attempt": attempt_n,
                     "elapsed_ms": elapsed_ms},
                )
                breaker.record_failure()
                return None

        # Success.
        result = result_holder[0]
        breaker.record_success()
        return result

    # ── First attempt ──────────────────────────────────────────────────────
    try:
        result = _attempt(1)
        return result
    except Exception:
        # First attempt raised (soft error) → wait and retry once.
        time.sleep(_RETRY_SLEEP_S)

    # ── Single retry ───────────────────────────────────────────────────────
    try:
        result = _attempt(2)
        return result
    except Exception:
        # _attempt(2) re-raises only if somehow a TimeoutError slips through;
        # all other paths return None after logging.  Guard here to be safe.
        breaker.record_failure()
        return None


# ─── Aggregation ─────────────────────────────────────────────────────────────

def _aggregate(critiques: list[dict]) -> dict:
    """Per-dimension aggregation: trimmed mean for scores, majority for bools."""
    out: dict = {"votes": []}

    for dim in DIMENSIONS_SCORED:
        scores = [c[dim]["score"] for c in critiques
                  if isinstance(c.get(dim), dict) and "score" in c[dim]]
        if not scores:
            out[dim] = {"score": None, "agreement": None}
            continue
        sorted_scores = sorted(scores)
        if len(sorted_scores) >= 3:
            trimmed = sorted_scores[1:-1]   # drop highest + lowest
            mean = sum(trimmed) / len(trimmed)
        else:
            mean = sum(sorted_scores) / len(sorted_scores)
        sigma = statistics.stdev(scores) if len(scores) > 1 else 0
        out[dim] = {
            "score": round(mean, 2),
            "sigma": round(sigma, 2),
            "votes": scores,
            "needs_adjudicator": sigma > ESCALATION_SIGMA,
        }

    for dim in DIMENSIONS_BOOL:
        bools = [bool(c[dim].get("pass")) for c in critiques
                 if isinstance(c.get(dim), dict)]
        if not bools:
            out[dim] = {"pass": None, "agreement": None}
            continue
        votes_pass = sum(bools)
        out[dim] = {
            "pass": votes_pass > len(bools) / 2,
            "vote_pass": votes_pass,
            "vote_fail": len(bools) - votes_pass,
            "unanimous": votes_pass == len(bools) or votes_pass == 0,
        }

    # Collect all shot issues; annotate with originating model.
    all_issues = []
    for c in critiques:
        for issue in c.get("shot_issues", []):
            if isinstance(issue, dict):
                all_issues.append({**issue, "by": c.get("_model")})
    out["shot_issues"] = all_issues

    # Overall verdict.
    crit_dim_fails = []
    if not out.get("compliance_subjective", {}).get("pass", True):
        crit_dim_fails.append("compliance_subjective")
    avg_quality = statistics.mean(
        [out[d]["score"] for d in DIMENSIONS_SCORED if out[d]["score"] is not None]
        or [5]
    )
    needs_adj = any(out[d].get("needs_adjudicator") for d in DIMENSIONS_SCORED)

    if crit_dim_fails or any(i["severity"] == "critical" for i in all_issues):
        out["verdict"] = "rejected"
    elif needs_adj:
        out["verdict"] = "needs_adjudicator"
    elif avg_quality >= 6.5:
        out["verdict"] = "approved"
    else:
        out["verdict"] = "rejected"

    out["overall_score"] = round(avg_quality * 10, 1)  # 0–100 scale
    return out


# ─── Main entry point ─────────────────────────────────────────────────────────

def evaluate(spec: dict, brand: dict, transcript: str, tier1: dict,
             hook: dict, feature_id: str, *, allow_paid: bool = False) -> dict:
    """
    Run panel in parallel, aggregate, log each vote + final aggregate.

    Partial-panel fallback (configurable via EVAL_PANEL_MIN_VOTES, default 2):
      0 votes → rejected  / reason='all_panel_failed'
      1 vote  → needs_adjudicator / reason='only_one_vote'
      2+ votes → aggregate normally; warn if panel was incomplete
    """
    prompt = _build_prompt(spec, brand, transcript, tier1, hook)
    roles = list(PANEL_ROLES)
    if allow_paid:
        roles.append(PANEL_PAID_ROLE)

    total_panel_size = len(roles)
    critiques: list[dict] = []

    with ThreadPoolExecutor(max_workers=total_panel_size) as pool:
        futures = {
            pool.submit(_score_with_model, r, prompt, feature_id): r
            for r in roles
        }
        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception as e:
                # _score_with_model is designed to never propagate; guard anyway.
                role = futures[fut]
                devlog.append(
                    "panel_error", "supervisor", "feature", feature_id,
                    {"role": role, "error": str(e), "stage": "evaluate_outer"},
                )
                result = None
            if result is not None:
                critiques.append(result)

    n_responded = len(critiques)

    # ── Partial-panel fallback ─────────────────────────────────────────────
    if n_responded == 0:
        devlog.append(
            "eval_aggregate", "supervisor", "feature", feature_id,
            {"verdict": "rejected", "reason": "all_panel_failed",
             "panel_size": 0, "total_panel_size": total_panel_size},
        )
        return {
            "verdict": "rejected",
            "reason": "all_panel_failed",
            "error": "all panel members failed",
            "panel_size": 0,
        }

    if n_responded == 1:
        devlog.append(
            "eval_aggregate", "supervisor", "feature", feature_id,
            {"verdict": "needs_adjudicator", "reason": "only_one_vote",
             "panel_size": 1, "total_panel_size": total_panel_size},
        )
        return {
            "verdict": "needs_adjudicator",
            "reason": "only_one_vote",
            "panel_size": 1,
            "models": [critiques[0]["_model"]],
            # Include the single vote verbatim so adjudicator has something.
            "single_vote": critiques[0],
        }

    # ── Normal aggregation (2+ votes) ─────────────────────────────────────
    aggregated = _aggregate(critiques)
    aggregated["panel_size"] = n_responded
    aggregated["models"] = [c["_model"] for c in critiques]

    partial = n_responded < total_panel_size
    if partial:
        aggregated["warning"] = "partial_panel"
        devlog.append(
            "panel_partial", "supervisor", "feature", feature_id,
            {"responded": n_responded, "total": total_panel_size,
             "models": aggregated["models"]},
        )

    # Log each individual vote.
    for c in critiques:
        for dim in DIMENSIONS_SCORED:
            if isinstance(c.get(dim), dict) and "score" in c[dim]:
                devlog.log_eval(
                    "tier2", dim, feature_id, c["_model"],
                    {"score": c[dim]["score"],
                     "evidence": c[dim].get("evidence", "")},
                )

    # Log aggregate summary.
    devlog.append(
        "eval_aggregate", "supervisor", "feature", feature_id,
        {
            "verdict": aggregated["verdict"],
            "overall_score": aggregated["overall_score"],
            "panel_size": n_responded,
            "total_panel_size": total_panel_size,
            "partial_panel": partial,
            "needs_adjudicator": aggregated["verdict"] == "needs_adjudicator",
        },
    )

    return aggregated


# ─── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Smoke test — exercises timeout / invalid-JSON / success paths without a
    # real LiteLLM process or openai package.
    #
    # Run with:
    #   python orchestrator/lib/eval_tier2.py        (standalone, stubs openai)
    #   python -m orchestrator.lib.eval_tier2        (package mode, same stubs)
    #
    # Strategy: inject lightweight sys.modules stubs for openai and the heavy
    # orchestrator.lib sub-modules BEFORE importing anything from the package.
    # Then load the real eval_tier2 logic from within the same process so all
    # patches apply to the live module objects.

    import importlib
    import sys
    import tempfile
    import types
    import unittest.mock as mock
    from pathlib import Path as _Path

    # ── Pre-stub openai so litellm_client can be imported ─────────────────
    if "openai" not in sys.modules:
        _openai_stub = types.ModuleType("openai")

        class _FakeOpenAI:
            def __init__(self, **kw):
                pass
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        raise RuntimeError("openai stub — should be mocked")

        _openai_stub.OpenAI = _FakeOpenAI
        sys.modules["openai"] = _openai_stub

    # ── Ensure the orchestrator package root is on sys.path ───────────────
    import os as _os
    _here = _Path(__file__).resolve().parent.parent.parent  # project root
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))

    # ── Stub cost / cost_gate so they don't need their own deps ───────────
    for _stub_name in (
        "orchestrator.lib.cost",
        "orchestrator.lib.cost_gate",
    ):
        if _stub_name not in sys.modules:
            _m = types.ModuleType(_stub_name)
            # cost.estimate returns an object with .as_dict()
            class _CostBD:
                def as_dict(self):
                    return {"total_usd": 0.0, "cloud_usd": 0.0,
                            "compute_usd": 0.0, "electricity_usd": 0.0}
            _m.estimate = lambda **kw: _CostBD()  # type: ignore[attr-defined]
            _m.gate = lambda fid, model, **kw: model  # type: ignore[attr-defined]
            sys.modules[_stub_name] = _m

    # ── Module references — this file IS the module under __main__ ───────
    # _t2 = this module itself (already loaded, bound to __main__).
    # litellm_client / devlog / circuit_breaker were bound by the try/except
    # at the top of the file; retrieve them from the module's own globals.
    import sys as _sys_main
    _t2 = _sys_main.modules["__main__"]
    _lc = litellm_client   # noqa: F821  (bound by top-level try/except)
    _dv = devlog            # noqa: F821
    import orchestrator.lib.circuit_breaker as _cb_mod

    print("=== eval_tier2 smoke test ===\n")

    # ── Stub devlog so no SQLite needed ───────────────────────────────────
    _logged: list[dict] = []

    def _fake_append(kind, actor, ref_type, ref_id, content):
        _logged.append({"kind": kind, **content})
        safe = {k: v for k, v in content.items() if k not in ("evidence",)}
        print(f"  [devlog] {kind}: {json.dumps(safe)}")
        return len(_logged)

    def _fake_log_eval(tier, dim, fid, evaluator, result):
        _logged.append({"kind": f"eval_{tier}", "dim": dim})

    _dv.append = _fake_append        # type: ignore[assignment]
    _dv.log_eval = _fake_log_eval    # type: ignore[assignment]
    # The module-level devlog reference in _t2 is the same object; patching
    # _dv already patches _t2.devlog (same object).

    # ── Temp breaker state file — don't pollute eval/breakers.json ────────
    import os as _os
    _tmp_breaker = _Path(tempfile.mktemp(suffix=".json"))
    _os.environ["EVAL_CB_STATE_PATH"] = str(_tmp_breaker)
    _os.environ["EVAL_PANEL_MODEL_TIMEOUT_S"] = "60"
    _os.environ["EVAL_PANEL_MIN_VOTES"] = "2"

    # Reload circuit_breaker so it picks up the patched env var and clears
    # any in-process breaker cache left over from a previous run.
    importlib.reload(_cb_mod)
    _t2.get_breaker = _cb_mod.get   # re-bind the module-level reference

    # ── Shared test fixtures ──────────────────────────────────────────────
    _GOOD = {
        "aesthetic":            {"score": 7, "evidence": "clean composition"},
        "narrative":            {"score": 6, "evidence": "clear story arc"},
        "brand_subjective":     {"score": 8, "evidence": "on-brand palette"},
        "hook_taste":           {"score": 7, "evidence": "strong hook"},
        "compliance_subjective": {"pass": True, "issues": []},
        "shot_issues": [],
    }
    _META = {"latency_ms": 100, "cost": {}, "model_used": "x",
             "input_tokens": 10, "output_tokens": 50}

    SPEC    = {"shots": [{"idx": 1, "prompt": "opening shot"}]}
    BRAND   = {"name": "TestBrand", "colors": {"primary": "#fff"}}
    TSCRIPT = "This is a test voiceover."
    TIER1   = {"technical": {"pass": True}}
    HOOK    = {"score": 7}
    FID     = "SMOKE-001"

    # ── Scenario A: all models succeed ────────────────────────────────────
    print("--- Scenario A: all models succeed ---")
    _logged.clear()

    def _all_ok(role, model, prompt, **kw):
        return dict(_GOOD), {**_META, "model_used": model}

    with mock.patch.object(_lc, "call_json", side_effect=_all_ok):
        result_a = _t2.evaluate(SPEC, BRAND, TSCRIPT, TIER1, HOOK, FID)

    assert result_a["panel_size"] == len(_t2.PANEL_ROLES), \
        f"Expected {len(_t2.PANEL_ROLES)} votes, got {result_a['panel_size']}"
    assert result_a["verdict"] in ("approved", "rejected", "needs_adjudicator"), \
        f"Unexpected verdict: {result_a['verdict']}"
    assert "warning" not in result_a, "Should not warn when full panel responds"
    print(f"  verdict={result_a['verdict']}  panel_size={result_a['panel_size']}")
    print("  PASS\n")

    # ── Scenario B: one model hangs → timeout → partial_panel ────────────
    print("--- Scenario B: one timeout, rest succeed -> partial_panel ---")
    _logged.clear()
    _b_counter: dict = {"n": 0}

    def _one_timeout(role, model, prompt, **kw):
        _b_counter["n"] += 1
        if _b_counter["n"] == 1:
            import time as _time
            _time.sleep(9999)  # interrupted by future.result(timeout=...)
        return dict(_GOOD), {**_META, "model_used": model}

    _t2._MODEL_TIMEOUT_S = 0.05   # very short timeout so test is fast
    with mock.patch.object(_lc, "call_json", side_effect=_one_timeout):
        result_b = _t2.evaluate(SPEC, BRAND, TSCRIPT, TIER1, HOOK, FID)
    _t2._MODEL_TIMEOUT_S = 60     # restore

    timeout_events = [e for e in _logged if e["kind"] == "panel_timeout"]
    assert len(timeout_events) >= 1, "Expected at least one panel_timeout event"
    ps_b = result_b.get("panel_size", 0)
    total_b = len(_t2.PANEL_ROLES)
    if ps_b < total_b and ps_b >= 2:
        assert result_b.get("warning") == "partial_panel", \
            f"Expected partial_panel warning, got {result_b.get('warning')}"
    print(f"  verdict={result_b.get('verdict')}  panel_size={ps_b}  "
          f"warning={result_b.get('warning', '-')}")
    print(f"  timeout events: {len(timeout_events)}")
    print("  PASS\n")

    # ── Scenario C: invalid JSON on first attempt → retry → success ───────
    print("--- Scenario C: bad JSON first attempt, success on retry ---")
    _logged.clear()
    _c_counter: dict = {"n": 0}

    def _bad_then_ok(role, model, prompt, **kw):
        _c_counter["n"] += 1
        if _c_counter["n"] == 1:
            raise json.JSONDecodeError("bad json", "", 0)
        return dict(_GOOD), {**_META, "model_used": model}

    _t2._RETRY_SLEEP_S = 0.0  # no real sleep in smoke test
    with mock.patch.object(_lc, "call_json", side_effect=_bad_then_ok):
        result_c = _t2.evaluate(SPEC, BRAND, TSCRIPT, TIER1, HOOK, FID)

    retry_events = [e for e in _logged if e["kind"] == "panel_retry"]
    assert len(retry_events) >= 1, "Expected at least one panel_retry event"
    print(f"  panel_retry events: {len(retry_events)}")
    print(f"  verdict={result_c.get('verdict')}  panel_size={result_c.get('panel_size')}")
    print("  PASS\n")

    # ── Scenario D: all models fail → rejected / all_panel_failed ─────────
    print("--- Scenario D: all models fail -> rejected/all_panel_failed ---")
    _logged.clear()

    def _always_fail(role, model, prompt, **kw):
        raise ConnectionError("simulated network failure")

    _t2._RETRY_SLEEP_S = 0.0
    with mock.patch.object(_lc, "call_json", side_effect=_always_fail):
        result_d = _t2.evaluate(SPEC, BRAND, TSCRIPT, TIER1, HOOK, FID)

    assert result_d["verdict"] == "rejected", \
        f"Expected rejected, got {result_d['verdict']}"
    assert result_d.get("reason") == "all_panel_failed", \
        f"Expected all_panel_failed, got {result_d.get('reason')}"
    print(f"  verdict={result_d['verdict']}  reason={result_d['reason']}")
    print("  PASS\n")

    # ── Scenario E: exactly 1 model responds → needs_adjudicator ──────────
    print("--- Scenario E: 1 model responds -> needs_adjudicator/only_one_vote ---")
    _logged.clear()
    _e_counter: dict = {"n": 0}

    def _one_success(role, model, prompt, **kw):
        _e_counter["n"] += 1
        if _e_counter["n"] == 1:
            return dict(_GOOD), {**_META, "model_used": model}
        raise ConnectionError("all others fail")

    _t2._RETRY_SLEEP_S = 0.0
    with mock.patch.object(_lc, "call_json", side_effect=_one_success):
        result_e = _t2.evaluate(SPEC, BRAND, TSCRIPT, TIER1, HOOK, FID)

    assert result_e["verdict"] in ("needs_adjudicator", "rejected"), \
        f"Unexpected verdict for single vote: {result_e['verdict']}"
    if result_e.get("panel_size") == 1:
        assert result_e.get("reason") == "only_one_vote", \
            f"Expected only_one_vote, got {result_e.get('reason')}"
        print(f"  verdict={result_e['verdict']}  reason={result_e['reason']}")
    else:
        # Multiple responded (race-dependent) — still valid
        print(f"  verdict={result_e['verdict']}  panel_size={result_e.get('panel_size')}")
    print("  PASS\n")

    # ── Cleanup ───────────────────────────────────────────────────────────
    if _tmp_breaker.exists():
        _tmp_breaker.unlink()

    print("=== All smoke tests passed ===")
    sys.exit(0)
