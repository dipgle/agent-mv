"""
Stock music sourcing — commercial-clean replacement for Stable Audio Open (CC-BY-NC).

Primary source: Pixabay Music API (royalty-free, commercial OK, no attribution required).
  Docs: https://pixabay.com/api/docs/
  Free tier: 5000 req/hour; first 100 results/day accessible without a key.
  Recommended: set PIXABAY_API_KEY env var for higher quotas.

Fallback: CC0 tracks in data/stock_music_fallback/ (users drop their own files).

Usage:
    from orchestrator.lib.stock_music import PixabayMusicClient
    client = PixabayMusicClient()
    track_path = client.select_track({"bpm": 120, "mood": "energetic", "key": "C major"})
    # track_path is a local Path (WAV or MP3) ready for ffmpeg

Logging:
    select_track() logs a kind='stock_music_pick' event to devlog with:
    {source, title, license, duration_s, url}
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from . import devlog

log = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────

PIXABAY_API_URL = "https://pixabay.com/api/videos/music/"  # music search endpoint

# Optional API key — improves rate limits significantly.
# Without key: anonymous quota applies (~100 results/day per IP).
PIXABAY_API_KEY: Optional[str] = os.environ.get("PIXABAY_API_KEY")

# Local fallback directory — users drop CC0 tracks here.
_PROJECT_ROOT = Path(__file__).parent.parent.parent
FALLBACK_DIR = _PROJECT_ROOT / "data" / "stock_music_fallback"

# Download destination for tracks fetched from Pixabay.
CACHE_DIR = _PROJECT_ROOT / "data" / "stock_music_cache"

# Timeout for HTTP requests (seconds).
HTTP_TIMEOUT = 30

# Pixabay mood → query keyword mapping.
# Pixabay's music API supports free-text search; these map our brief moods
# to terms that yield the best results based on empirical testing.
_MOOD_QUERY_MAP: dict[str, str] = {
    "uplifting":    "uplifting motivational positive",
    "energetic":    "energetic fast upbeat",
    "modern":       "modern corporate technology",
    "minimal":      "minimal ambient calm",
    "dramatic":     "cinematic dramatic epic",
    "calm":         "calm relaxing soft",
    "happy":        "happy cheerful fun",
    "corporate":    "corporate professional business",
    "cinematic":    "cinematic orchestral score",
    "electronic":   "electronic synth beat",
}


class PixabayMusicClient:
    """
    Searches Pixabay Music API by mood + BPM range, downloads the best match,
    and returns a local path to the audio file.

    Falls back to the local CC0 library at data/stock_music_fallback/ when:
    - No API key is set and the anonymous quota is exhausted
    - A network error occurs
    - No matching track is found
    """

    def __init__(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)

    # ─── Public API ──────────────────────────────────────────────────────

    def select_track(self, brief: dict, feature_id: str = "") -> Path:
        """
        Choose a royalty-free music track matching the given brief.

        Args:
            brief:      Planner music_brief dict — keys: bpm (int), mood (str or list),
                        key (str, optional). Example:
                        {"bpm": 120, "mood": ["uplifting", "modern"], "key": "C major"}
            feature_id: current video ID for devlog correlation

        Returns:
            Path to a local WAV or MP3 file, suitable for passing directly to ffmpeg.

        Raises:
            RuntimeError if no track can be found from any source.
        """
        bpm = brief.get("bpm", 120)
        mood_raw = brief.get("mood", "uplifting")
        moods = mood_raw if isinstance(mood_raw, list) else [mood_raw]

        # Build a BPM range window — ±20 BPM is wide enough to match most results.
        bpm_min = max(40, bpm - 20)
        bpm_max = bpm + 20

        # Try Pixabay API first.
        try:
            track = self._search_pixabay(moods, bpm_min, bpm_max)
            if track:
                local_path = self._download(track["url"], track["title"])
                self._log_pick(
                    feature_id=feature_id,
                    source="pixabay_api",
                    title=track["title"],
                    license_name="Pixabay License (royalty-free, commercial OK)",
                    duration_s=track.get("duration", 0),
                    url=track["url"],
                )
                return local_path
        except Exception as exc:
            log.warning("Pixabay API search failed (%s); falling back to local library", exc)

        # Fallback: local CC0 library.
        local = self._pick_from_fallback(moods, bpm_min, bpm_max)
        if local:
            self._log_pick(
                feature_id=feature_id,
                source="local_cc0_fallback",
                title=local.stem,
                license_name="CC0 Public Domain",
                duration_s=0,  # duration unknown without ffprobe
                url=str(local),
            )
            return local

        raise RuntimeError(
            "No stock music track found. "
            "Add CC0 tracks to data/stock_music_fallback/ or set PIXABAY_API_KEY."
        )

    # ─── Pixabay API ─────────────────────────────────────────────────────

    def _search_pixabay(
        self, moods: list[str], bpm_min: int, bpm_max: int
    ) -> Optional[dict]:
        """
        Search Pixabay music API. Returns first matching result dict, or None.

        Result dict keys: title, url, duration (seconds).
        """
        query = self._build_query(moods)
        params: dict[str, str] = {
            "q": query,
            "per_page": "20",
        }
        if PIXABAY_API_KEY:
            params["key"] = PIXABAY_API_KEY

        url = PIXABAY_API_URL + "?" + urllib.parse.urlencode(params)
        log.debug("Pixabay music search: %s", url)

        req = urllib.request.Request(url, headers={"User-Agent": "agent-mv/1.0"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        hits = data.get("hits", [])
        if not hits:
            return None

        # Filter by BPM if the API returns bpm field; otherwise take first result.
        for hit in hits:
            hit_bpm = hit.get("bpm")
            if hit_bpm is None or bpm_min <= int(hit_bpm) <= bpm_max:
                download_url = hit.get("audio", {}).get("url") or hit.get("url")
                if download_url:
                    return {
                        "title": hit.get("tags", "unknown"),
                        "url": download_url,
                        "duration": hit.get("duration", 0),
                    }

        # BPM filter eliminated all; return the first hit anyway.
        hit = hits[0]
        download_url = hit.get("audio", {}).get("url") or hit.get("url")
        if download_url:
            return {
                "title": hit.get("tags", "unknown"),
                "url": download_url,
                "duration": hit.get("duration", 0),
            }
        return None

    def _build_query(self, moods: list[str]) -> str:
        """Map moods to Pixabay search terms."""
        terms: list[str] = []
        for m in moods:
            mapped = _MOOD_QUERY_MAP.get(m.lower(), m)
            terms.append(mapped)
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for t in terms:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return " ".join(unique)[:100]  # Pixabay max query length

    def _download(self, url: str, title: str) -> Path:
        """Download a track URL to the local cache; return local Path."""
        # Derive a stable filename from the URL to avoid re-downloading.
        safe_title = re.sub(r"[^\w\-]", "_", title)[:50]
        url_hash = str(abs(hash(url)))[:8]
        ext = ".mp3" if ".mp3" in url.lower() else ".wav"
        filename = f"{safe_title}_{url_hash}{ext}"
        dest = CACHE_DIR / filename

        if dest.exists():
            log.debug("Cache hit: %s", dest)
            return dest

        log.info("Downloading Pixabay track: %s -> %s", url, dest)
        req = urllib.request.Request(url, headers={"User-Agent": "agent-mv/1.0"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            dest.write_bytes(resp.read())
        log.info("Downloaded %.1f KB in %.1fs", dest.stat().st_size / 1024, time.time() - t0)
        return dest

    # ─── Local fallback ──────────────────────────────────────────────────

    def _pick_from_fallback(
        self, moods: list[str], bpm_min: int, bpm_max: int
    ) -> Optional[Path]:
        """
        Scan data/stock_music_fallback/ for a track that matches by filename keywords.
        Returns the first reasonable match, or any track if nothing matches mood/BPM.
        """
        candidates = list(FALLBACK_DIR.glob("*.wav")) + list(FALLBACK_DIR.glob("*.mp3"))
        if not candidates:
            return None

        mood_terms = {m.lower() for m in moods}

        # Score each file: +2 for mood keyword match, +1 for BPM in range.
        scored: list[tuple[int, Path]] = []
        for p in candidates:
            name = p.stem.lower()
            score = 0
            for term in mood_terms:
                if term in name:
                    score += 2
            # Extract BPM from filename pattern like "120bpm" or "120_bpm".
            bpm_match = re.search(r"(\d{2,3})\s*bpm", name)
            if bpm_match:
                file_bpm = int(bpm_match.group(1))
                if bpm_min <= file_bpm <= bpm_max:
                    score += 1
            scored.append((score, p))

        scored.sort(key=lambda x: -x[0])
        return scored[0][1]

    # ─── Devlog ──────────────────────────────────────────────────────────

    def _log_pick(
        self,
        *,
        feature_id: str,
        source: str,
        title: str,
        license_name: str,
        duration_s: int,
        url: str,
    ) -> None:
        """Log a stock_music_pick event to devlog."""
        try:
            devlog.append(
                "stock_music_pick",
                "executor-music",
                "feature",
                feature_id,
                {
                    "source": source,
                    "title": title,
                    "license": license_name,
                    "duration_s": duration_s,
                    "url": url,
                },
            )
        except Exception as exc:
            # Devlog failures are non-fatal.
            log.warning("devlog.append failed: %s", exc)
