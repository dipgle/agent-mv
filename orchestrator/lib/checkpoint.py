"""
Checkpoint system for the video production pipeline.

Persists progress to <feature_dir>/.checkpoint.json so that a crashed or
interrupted run can resume exactly where it left off without re-rendering
already-completed steps.

Step ID convention
------------------
researcher                  -- entire Researcher role
planner                     -- entire Planner role
shot_NN_keyframe            -- per-shot keyframe (NN zero-padded, e.g. shot_01_keyframe)
shot_NN_motion              -- per-shot motion clip
voice                       -- whole-video voiceover
music                       -- whole-video BGM
caption                     -- whole-video SRT subtitles
compose                     -- final ffmpeg compose pass
reviewer_attempt_N          -- per-attempt reviewer verdict (never auto-skipped,
                               but its result is recorded so downstream logic
                               can inspect it without re-running LLM)

Schema (schema_version=1)
--------------------------
{
  "schema_version": 1,
  "feature_id": "VID-001",
  "started_at": "2026-06-09T12:00:00",
  "completed_steps": ["researcher", "planner", "shot_01_keyframe", ...],
  "last_step": "shot_03_motion",
  "artifacts": {
    "shot_01_keyframe": "out/VID-001/shots/01_keyframe.png",
    ...
  },
  "attempt_n": 1,
  "superseded_compose": ["out/VID-001/final_attempt1.mp4"]
}
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional


SCHEMA_VERSION = 1
CHECKPOINT_FILENAME = ".checkpoint.json"

# Maximum attempt count before we warn the operator.
HIGH_ATTEMPT_THRESHOLD = 5


class CheckpointStore:
    """
    Manages checkpoint state for a single feature/video render session.

    All writes are atomic: data is written to a temp file in the same directory
    then renamed over the target, so a crash mid-write never corrupts the
    existing checkpoint.
    """

    def __init__(self, feature_dir: Path, feature_id: str) -> None:
        self.feature_dir = Path(feature_dir)
        self.feature_id = feature_id
        self._path = self.feature_dir / CHECKPOINT_FILENAME
        self._data: dict = {}

    # ── Factory methods ────────────────────────────────────────────────────

    @classmethod
    def load(cls, feature_dir: Path, feature_id: str = "") -> "CheckpointStore":
        """
        Load an existing checkpoint from feature_dir or create a fresh one.

        If the checkpoint file is present but unparseable it is discarded and
        a fresh checkpoint is returned (failure-safe: never block a run).
        """
        store = cls(Path(feature_dir), feature_id)
        cp_path = store._path

        if cp_path.exists():
            try:
                raw = json.loads(cp_path.read_text(encoding="utf-8"))
                if raw.get("schema_version") == SCHEMA_VERSION:
                    store._data = raw
                    # Backfill feature_id if caller did not supply it.
                    if not feature_id:
                        store.feature_id = raw.get("feature_id", "")
                    return store
            except (json.JSONDecodeError, OSError):
                # Corrupted file — start fresh, the corrupt file will be
                # overwritten on the next mark_step_done call.
                pass

        # Fresh init.
        store._data = {
            "schema_version": SCHEMA_VERSION,
            "feature_id": feature_id,
            "started_at": datetime.utcnow().isoformat(),
            "completed_steps": [],
            "last_step": None,
            "artifacts": {},
            "attempt_n": 1,
            "superseded_compose": [],
        }
        return store

    # ── Read helpers ───────────────────────────────────────────────────────

    def is_done(self, step_id: str) -> bool:
        """Return True if step_id has been successfully completed."""
        return step_id in self._data.get("completed_steps", [])

    def get_artifact(self, step_id: str) -> Optional[Path]:
        """
        Return the Path recorded for step_id, or None if not present.

        Also returns None when the path no longer exists on disk — the caller
        should treat a missing file as an invalid step (see repair_missing).
        """
        raw = self._data.get("artifacts", {}).get(step_id)
        if raw is None:
            return None
        p = Path(raw)
        if not p.exists():
            return None
        return p

    @property
    def attempt_n(self) -> int:
        return int(self._data.get("attempt_n", 1))

    @property
    def completed_steps(self) -> list[str]:
        return list(self._data.get("completed_steps", []))

    @property
    def artifacts(self) -> dict[str, str]:
        return dict(self._data.get("artifacts", {}))

    # ── Write helpers ──────────────────────────────────────────────────────

    def mark_step_done(
        self, step_id: str, artifact_path: Optional[str | Path] = None
    ) -> None:
        """
        Record step_id as completed and optionally save its output path.

        Write is atomic: temp file + rename so a crash mid-write leaves the
        previous checkpoint intact.
        """
        steps = self._data.setdefault("completed_steps", [])
        if step_id not in steps:
            steps.append(step_id)
        self._data["last_step"] = step_id

        if artifact_path is not None:
            self._data.setdefault("artifacts", {})[step_id] = str(artifact_path)

        self._atomic_save()

    def next_attempt(self) -> None:
        """
        Increment attempt_n and stash the previous compose path as superseded.

        Call this at the start of each Reviewer-rejected re-render loop so the
        checkpoint tracks how many full passes have been attempted.
        """
        prev_compose = self._data.get("artifacts", {}).get("compose")
        if prev_compose:
            superseded = self._data.setdefault("superseded_compose", [])
            if prev_compose not in superseded:
                superseded.append(prev_compose)
            # Remove compose from completed so it is re-run on the new attempt.
            self._data["completed_steps"] = [
                s for s in self._data.get("completed_steps", [])
                if s != "compose"
            ]
            self._data.get("artifacts", {}).pop("compose", None)

        self._data["attempt_n"] = self.attempt_n + 1
        self._atomic_save()

    def invalidate_steps(self, step_ids: list[str]) -> None:
        """
        Remove specific steps from completed_steps and their artifacts.

        Used when the Reviewer flags only certain shots for re-render — those
        shots are invalidated while all other completed steps are preserved.
        """
        step_set = set(step_ids)
        self._data["completed_steps"] = [
            s for s in self._data.get("completed_steps", [])
            if s not in step_set
        ]
        for sid in step_ids:
            self._data.get("artifacts", {}).pop(sid, None)
        self._atomic_save()

    def reset(self, scope: str = "all") -> None:
        """
        Partial or full checkpoint invalidation.

        scope values:
          'all'     -- wipe everything, keep feature_id + attempt_n + started_at
          'shots'   -- invalidate only shot_NN_keyframe / shot_NN_motion steps
          'audio'   -- invalidate voice / music / caption
          'compose' -- invalidate compose only
        """
        if scope == "all":
            self._data["completed_steps"] = []
            self._data["artifacts"] = {}
            self._data["last_step"] = None
        elif scope == "shots":
            self._data["completed_steps"] = [
                s for s in self._data.get("completed_steps", [])
                if not (s.startswith("shot_") and
                        (s.endswith("_keyframe") or s.endswith("_motion")))
            ]
            for k in list(self._data.get("artifacts", {}).keys()):
                if k.startswith("shot_") and (k.endswith("_keyframe") or k.endswith("_motion")):
                    del self._data["artifacts"][k]
        elif scope == "audio":
            audio_steps = {"voice", "music", "caption"}
            self._data["completed_steps"] = [
                s for s in self._data.get("completed_steps", [])
                if s not in audio_steps
            ]
            for s in audio_steps:
                self._data.get("artifacts", {}).pop(s, None)
        elif scope == "compose":
            self.invalidate_steps(["compose"])
        else:
            raise ValueError(f"Unknown reset scope: {scope!r}. "
                             f"Expected 'all', 'shots', 'audio', or 'compose'.")
        self._atomic_save()

    def repair_missing(self) -> list[str]:
        """
        Scan artifacts for paths that no longer exist on disk and invalidate them.

        Returns the list of step IDs that were removed so the caller can log a
        kind='checkpoint_repair' event.
        """
        missing: list[str] = []
        for step_id, path_str in list(self._data.get("artifacts", {}).items()):
            if not Path(path_str).exists():
                missing.append(step_id)

        if missing:
            self.invalidate_steps(missing)

        return missing

    # ── Health warnings ────────────────────────────────────────────────────

    def high_attempt_warning(self) -> Optional[str]:
        """
        Return a warning string if attempt_n exceeds the threshold, else None.

        The pipeline prints this at startup so operators can intervene before
        burning more compute on a video that may have a structural problem.
        """
        if self.attempt_n > HIGH_ATTEMPT_THRESHOLD:
            return (
                f"[WARN] Checkpoint for {self.feature_id!r} is on attempt "
                f"{self.attempt_n} (threshold: {HIGH_ATTEMPT_THRESHOLD}). "
                f"Manual inspection suggested before continuing. "
                f"Use --show-checkpoint to inspect or --force-redo to restart clean."
            )
        return None

    # ── Display ────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Return a human-readable one-line summary of the checkpoint state."""
        n_done = len(self._data.get("completed_steps", []))
        last = self._data.get("last_step") or "none"
        return (
            f"feature={self.feature_id} attempt={self.attempt_n} "
            f"steps_done={n_done} last_step={last}"
        )

    def pretty_print(self) -> str:
        """Return a multi-line human-readable dump for --show-checkpoint."""
        lines = [
            f"Checkpoint: {self._path}",
            f"  feature_id : {self._data.get('feature_id')}",
            f"  started_at : {self._data.get('started_at')}",
            f"  attempt_n  : {self.attempt_n}",
            f"  last_step  : {self._data.get('last_step')}",
            "",
            "  Completed steps:",
        ]
        for s in self._data.get("completed_steps", []):
            art = self._data.get("artifacts", {}).get(s, "")
            art_str = f"  →  {art}" if art else ""
            lines.append(f"    {s}{art_str}")

        if self._data.get("superseded_compose"):
            lines.append("")
            lines.append("  Superseded compose outputs:")
            for p in self._data["superseded_compose"]:
                lines.append(f"    {p}")
        return "\n".join(lines)

    # ── Internal ───────────────────────────────────────────────────────────

    def _atomic_save(self) -> None:
        """Write checkpoint JSON atomically via tmp-file + rename."""
        payload = json.dumps(self._data, indent=2, ensure_ascii=False)
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)

        # Write to a sibling temp file, then rename — atomic on POSIX and
        # sufficiently safe on Windows (os.replace is atomic on Win32 too).
        fd, tmp_path = tempfile.mkstemp(
            dir=str(parent), prefix=".checkpoint_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_path, str(self._path))
        except Exception:
            # Best-effort cleanup of temp file on unexpected error.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ── Module-level helpers used by pipeline.py ─────────────────────────────────

def load(feature_dir: Path, feature_id: str = "") -> CheckpointStore:
    """Convenience alias: CheckpointStore.load(feature_dir, feature_id)."""
    return CheckpointStore.load(Path(feature_dir), feature_id)


def step_id_shot(shot_idx: int, kind: str) -> str:
    """
    Build a canonical step ID for a per-shot step.

    kind should be 'keyframe' or 'motion'.
    """
    return f"shot_{shot_idx:02d}_{kind}"


def step_id_reviewer(attempt_n: int) -> str:
    """Build a canonical step ID for a reviewer verdict on attempt N."""
    return f"reviewer_attempt_{attempt_n}"


# ── Smoke test (standalone, no devlog, no ComfyUI) ───────────────────────────

if __name__ == "__main__":
    import sys
    import tempfile

    print("=== CheckpointStore smoke test ===")
    errors: list[str] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        fdir = Path(tmpdir)

        # 1. Fresh init
        cp = CheckpointStore.load(fdir, "SMOKE-001")
        assert cp.attempt_n == 1, f"Expected attempt_n=1, got {cp.attempt_n}"
        assert cp.completed_steps == [], "Fresh checkpoint should have no steps"
        print("  [OK] fresh init")

        # 2. Mark some steps done
        cp.mark_step_done("researcher")
        cp.mark_step_done("planner")
        cp.mark_step_done("shot_01_keyframe", artifact_path="/tmp/01_kf.png")
        cp.mark_step_done("shot_01_motion", artifact_path="/tmp/01_clip.mp4")
        cp.mark_step_done("shot_02_keyframe", artifact_path="/tmp/02_kf.png")
        assert cp.is_done("researcher"), "researcher should be done"
        assert cp.is_done("shot_01_keyframe"), "shot_01_keyframe should be done"
        assert not cp.is_done("voice"), "voice should not be done yet"
        print("  [OK] mark_step_done + is_done")

        # 3. Reload from disk — verifies atomic write + load
        cp2 = CheckpointStore.load(fdir, "SMOKE-001")
        assert cp2.is_done("researcher"), "researcher should survive reload"
        assert cp2.is_done("shot_01_motion"), "shot_01_motion should survive reload"
        assert len(cp2.completed_steps) == 5, (
            f"Expected 5 completed steps, got {len(cp2.completed_steps)}: {cp2.completed_steps}"
        )
        print("  [OK] reload from disk")

        # 4. Partial reset: scope='shots'
        cp2.reset(scope="shots")
        assert cp2.is_done("researcher"), "researcher should survive shots reset"
        assert cp2.is_done("planner"), "planner should survive shots reset"
        assert not cp2.is_done("shot_01_keyframe"), "shot_01_keyframe should be cleared"
        assert not cp2.is_done("shot_02_keyframe"), "shot_02_keyframe should be cleared"
        print("  [OK] reset(scope='shots')")

        # 5. Audio reset
        cp2.mark_step_done("voice", artifact_path="/tmp/voice.wav")
        cp2.mark_step_done("music", artifact_path="/tmp/bgm.wav")
        cp2.mark_step_done("caption")
        cp2.reset(scope="audio")
        assert not cp2.is_done("voice"), "voice should be cleared after audio reset"
        assert not cp2.is_done("music"), "music should be cleared after audio reset"
        assert cp2.is_done("researcher"), "researcher should survive audio reset"
        print("  [OK] reset(scope='audio')")

        # 6. next_attempt
        cp2.mark_step_done("compose", artifact_path="/tmp/final.mp4")
        assert cp2.is_done("compose"), "compose should be done"
        cp2.next_attempt()
        assert cp2.attempt_n == 2, f"Expected attempt_n=2, got {cp2.attempt_n}"
        assert not cp2.is_done("compose"), "compose should be cleared after next_attempt"
        assert "/tmp/final.mp4" in cp2._data.get("superseded_compose", []), \
            "old compose should appear in superseded_compose"
        print("  [OK] next_attempt increments + supersedes compose")

        # 7. invalidate_steps (partial shot re-render)
        cp2.mark_step_done("shot_01_keyframe", artifact_path="/tmp/01_kf_v2.png")
        cp2.mark_step_done("shot_02_keyframe", artifact_path="/tmp/02_kf_v2.png")
        cp2.mark_step_done("shot_03_keyframe", artifact_path="/tmp/03_kf_v2.png")
        cp2.invalidate_steps(["shot_02_keyframe"])
        assert cp2.is_done("shot_01_keyframe"), "shot_01 should remain"
        assert not cp2.is_done("shot_02_keyframe"), "shot_02 should be invalidated"
        assert cp2.is_done("shot_03_keyframe"), "shot_03 should remain"
        print("  [OK] invalidate_steps (partial shot)")

        # 8. repair_missing — artifact path doesn't actually exist
        cp2.mark_step_done("shot_04_keyframe", artifact_path="/nonexistent/path.png")
        assert cp2.is_done("shot_04_keyframe"), "should appear done before repair"
        repaired = cp2.repair_missing()
        assert "shot_04_keyframe" in repaired, "missing path should be repaired"
        assert not cp2.is_done("shot_04_keyframe"), "should be invalid after repair"
        print("  [OK] repair_missing")

        # 9. high_attempt_warning
        cp2._data["attempt_n"] = HIGH_ATTEMPT_THRESHOLD + 1
        warning = cp2.high_attempt_warning()
        assert warning is not None, "should warn at high attempt"
        assert "WARN" in warning, "warning should contain WARN"
        cp2._data["attempt_n"] = 2
        assert cp2.high_attempt_warning() is None, "no warning at low attempt"
        print("  [OK] high_attempt_warning")

        # 10. full reset
        cp2.reset(scope="all")
        assert cp2.completed_steps == [], "all steps should be cleared"
        assert cp2.artifacts == {}, "all artifacts should be cleared"
        print("  [OK] reset(scope='all')")

        # 11. step_id helpers
        assert step_id_shot(1, "keyframe") == "shot_01_keyframe"
        assert step_id_shot(12, "motion") == "shot_12_motion"
        assert step_id_reviewer(3) == "reviewer_attempt_3"
        print("  [OK] step_id helpers")

        # 12. pretty_print / summary (just verify no crash)
        cp2.mark_step_done("researcher")
        _ = cp2.pretty_print()
        _ = cp2.summary()
        print("  [OK] pretty_print + summary")

    if errors:
        print("\nFAILED:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        print("\nAll checks passed.")
