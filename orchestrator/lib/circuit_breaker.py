"""
Circuit breaker for LLM panel models — sliding window, per-model state.

State is persisted in eval/breakers.json (atomic tmp+rename write) so
breaker memory survives process restarts.  The file is gitignored.

Defaults (all overridable via env):
  EVAL_CB_FAILURE_THRESHOLD   = 3   failures in the window to open
  EVAL_CB_WINDOW_CALLS        = 30  sliding window size (calls)
  EVAL_CB_OPEN_DURATION_S     = 300 seconds breaker stays open (5 min)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock

# ─── Configuration ────────────────────────────────────────────────────────────

FAILURE_THRESHOLD: int = int(os.environ.get("EVAL_CB_FAILURE_THRESHOLD", "3"))
WINDOW_CALLS: int      = int(os.environ.get("EVAL_CB_WINDOW_CALLS", "30"))
OPEN_DURATION_S: float = float(os.environ.get("EVAL_CB_OPEN_DURATION_S", "300"))

# Path to persistent state file (relative to project root).
# The serving process cwd is expected to be the repo root; callers that set
# a different cwd should set EVAL_CB_STATE_PATH explicitly.
_DEFAULT_STATE_PATH = Path(
    os.environ.get("EVAL_CB_STATE_PATH", "eval/breakers.json")
)

# Module-level lock so concurrent panel threads share one in-process view.
_lock = Lock()

# In-memory cache: {model -> CircuitBreaker}
_breakers: dict[str, "CircuitBreaker"] = {}


# ─── Helper: atomic JSON file read/write ─────────────────────────────────────

def _load_state(path: Path) -> dict:
    """Read persisted state; return empty dict if file missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    """Atomic write via tmp+rename (cross-platform safe on POSIX and Windows).

    Uses a unique suffix so concurrent calls from multiple threads don't clobber
    each other's temp file before the rename.
    """
    import os as _os
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique tmp name: avoids races when multiple models persist simultaneously.
    tmp = path.with_name(f"{path.stem}_{_os.getpid()}_{id(state)}.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX; best-effort on Windows


# ─── CircuitBreaker ───────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Sliding-window circuit breaker for a single model.

    State machine:
      CLOSED  — normal operation; calls go through
      OPEN    — too many recent failures; calls are skipped for open_duration_s
               (half-open recheck happens automatically after the cooldown)
    """

    def __init__(
        self,
        model: str,
        state_path: Path = _DEFAULT_STATE_PATH,
        failure_threshold: int = FAILURE_THRESHOLD,
        window_calls: int = WINDOW_CALLS,
        open_duration_s: float = OPEN_DURATION_S,
    ) -> None:
        self.model = model
        self.state_path = state_path
        self.failure_threshold = failure_threshold
        self.window_calls = window_calls
        self.open_duration_s = open_duration_s

        # Sliding window: list of bools (True=success, False=failure),
        # oldest first; capped at window_calls entries.
        self._window: list[bool] = []
        # Unix timestamp when the breaker was opened; 0 = not open.
        self._open_since: float = 0.0

        self._restore()

    # ── Serialisation helpers ─────────────────────────────────────────────

    def _to_dict(self) -> dict:
        return {
            "model": self.model,
            "window": self._window,
            "open_since": self._open_since,
        }

    def _from_dict(self, d: dict) -> None:
        self._window    = list(d.get("window", []))
        self._open_since = float(d.get("open_since", 0.0))

    def _restore(self) -> None:
        """Load this model's state from the shared JSON file."""
        state = _load_state(self.state_path)
        if self.model in state:
            self._from_dict(state[self.model])

    def _persist(self) -> None:
        """Write this model's state back to the shared JSON file.

        Serialised through the module lock so concurrent record_success /
        record_failure calls from multiple threads don't race on the JSON
        read-modify-write cycle.
        """
        with _lock:
            state = _load_state(self.state_path)
            state[self.model] = self._to_dict()
            _save_state(self.state_path, state)

    # ── Public API ────────────────────────────────────────────────────────

    def is_open(self) -> bool:
        """
        Return True if the breaker is currently open (calls should be skipped).

        If the cooldown has elapsed, the breaker resets to CLOSED automatically
        so the next call acts as a half-open probe.
        """
        if self._open_since == 0.0:
            return False
        elapsed = time.time() - self._open_since
        if elapsed >= self.open_duration_s:
            # Cooldown expired — reset to CLOSED and give the model a chance.
            self._open_since = 0.0
            self._window.clear()
            self._persist()
            return False
        return True

    def record_success(self) -> None:
        """Record a successful call; slides the window."""
        self._window.append(True)
        self._trim_window()
        self._persist()

    def record_failure(self) -> None:
        """
        Record a failed call.  If failures in the current window breach the
        threshold, the breaker opens.
        """
        self._window.append(False)
        self._trim_window()
        failures = sum(1 for ok in self._window if not ok)
        if failures >= self.failure_threshold and self._open_since == 0.0:
            self._open_since = time.time()
        self._persist()

    # ── Internal ─────────────────────────────────────────────────────────

    def _trim_window(self) -> None:
        """Keep only the last window_calls entries."""
        if len(self._window) > self.window_calls:
            self._window = self._window[-self.window_calls :]

    def state_dict(self) -> dict:
        """Return a snapshot suitable for logging."""
        failures = sum(1 for ok in self._window if not ok)
        return {
            "model": self.model,
            "open": self.is_open(),
            "open_since": self._open_since or None,
            "window_size": len(self._window),
            "failures_in_window": failures,
        }


# ─── Module-level accessor (singleton per model) ──────────────────────────────

def get(model: str, state_path: Path = _DEFAULT_STATE_PATH) -> CircuitBreaker:
    """
    Return the shared CircuitBreaker instance for `model`.

    Instances are cached in process memory; `state_path` is only consulted on
    first access per model.
    """
    with _lock:
        if model not in _breakers:
            _breakers[model] = CircuitBreaker(model, state_path=state_path)
        return _breakers[model]
