"""
Outcome clients — fetch real engagement metrics post-publish.

This is Tier 4 of the eval architecture: the only ground truth we trust
more than LLM panels.  All clients return the same shape so calibration
queries don't care which platform a video was published on.

Common output schema (per platform observation):
    {
        "platform":         "youtube" | "tiktok" | "meta",
        "published_at":     ISO8601,
        "fetched_at":       ISO8601,
        "impressions":      int,
        "watch_through_pct": float (0-1),
        "avg_watch_s":      float,
        "engagement_rate":  float (likes+comments+shares / impressions),
        "ctr":              float,
        "conversion_n":     int,
    }

Each client tolerates missing fields and returns None for unknown values
rather than raising.  Auth bootstrap is platform-specific; see each class
docstring for the env vars required.

Manual fallback (ManualOutcomeClient) lets a human paste raw metrics
when API access is not yet set up — useful while the project is small.
"""

from __future__ import annotations
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import devlog


@dataclass
class Outcome:
    platform: str
    published_at: str | None = None
    fetched_at: str = ""
    impressions: int = 0
    watch_through_pct: float | None = None
    avg_watch_s: float | None = None
    engagement_rate: float | None = None
    ctr: float | None = None
    conversion_n: int = 0
    raw: dict | None = None       # provider response for audit

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at or datetime.now(timezone.utc).isoformat(),
            "impressions": self.impressions,
            "watch_through_pct": self.watch_through_pct,
            "avg_watch_s": self.avg_watch_s,
            "engagement_rate": self.engagement_rate,
            "ctr": self.ctr,
            "conversion_n": self.conversion_n,
        }


class OutcomeClient(ABC):
    platform: str = "?"

    @abstractmethod
    def fetch(self, video_id: str, feature_id: str = "") -> Outcome | None:
        """Fetch metrics. Returns None on auth/lookup failure."""


# ─── YouTube ──────────────────────────────────────────────────────────────
class YouTubeClient(OutcomeClient):
    """
    Uses YouTube Data API v3 + YouTube Analytics API.
    Setup:
      1. Create GCP project, enable YouTube Data API v3 + YouTube Analytics
      2. Create OAuth2 credentials, download client_secrets.json
      3. `python -c "import google_auth_oauthlib; ..."` to seed refresh token
      4. Set env: YOUTUBE_API_KEY, YOUTUBE_REFRESH_TOKEN, YOUTUBE_CHANNEL_ID
    Doc: https://developers.google.com/youtube/analytics
    """
    platform = "youtube"

    def __init__(self) -> None:
        self.api_key = os.environ.get("YOUTUBE_API_KEY", "")
        self.refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
        self.channel_id = os.environ.get("YOUTUBE_CHANNEL_ID", "")

    def fetch(self, video_id: str, feature_id: str = "") -> Outcome | None:
        if not (self.api_key and self.refresh_token):
            devlog.append("outcome_skip", "supervisor", "feature", feature_id,
                          {"platform": "youtube",
                           "reason": "missing YOUTUBE_API_KEY or YOUTUBE_REFRESH_TOKEN",
                           "video_id": video_id})
            return None

        import requests

        # 1. Get OAuth token from refresh token
        try:
            token_resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": os.environ.get("YOUTUBE_CLIENT_ID", ""),
                    "client_secret": os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=15,
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]
        except Exception as e:
            devlog.append("outcome_error", "supervisor", "feature", feature_id,
                          {"platform": "youtube", "stage": "oauth", "error": str(e)})
            return None

        # 2. Public stats (impressions, likes, comments)
        try:
            stats = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics,snippet", "id": video_id, "key": self.api_key},
                timeout=15,
            ).json()
            item = (stats.get("items") or [{}])[0]
            published_at = item.get("snippet", {}).get("publishedAt")
            s = item.get("statistics", {})
            views = int(s.get("viewCount", 0))
            likes = int(s.get("likeCount", 0))
            comments = int(s.get("commentCount", 0))
        except Exception as e:
            devlog.append("outcome_error", "supervisor", "feature", feature_id,
                          {"platform": "youtube", "stage": "stats", "error": str(e)})
            return None

        # 3. Analytics retention (averageViewPercentage)
        avg_view_pct = None
        avg_view_s = None
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            ana = requests.get(
                "https://youtubeanalytics.googleapis.com/v2/reports",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "ids": f"channel=={self.channel_id}",
                    "startDate": "2025-01-01",
                    "endDate": today,
                    "metrics": "averageViewPercentage,averageViewDuration",
                    "filters": f"video=={video_id}",
                },
                timeout=15,
            ).json()
            rows = ana.get("rows") or []
            if rows:
                avg_view_pct = float(rows[0][0]) / 100  # 0-1
                avg_view_s = float(rows[0][1])
        except Exception as e:
            devlog.append("outcome_error", "supervisor", "feature", feature_id,
                          {"platform": "youtube", "stage": "analytics", "error": str(e)})

        engagement = (likes + comments) / views if views else None
        return Outcome(
            platform="youtube",
            published_at=published_at,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            impressions=views,
            watch_through_pct=avg_view_pct,
            avg_watch_s=avg_view_s,
            engagement_rate=engagement,
            raw={"stats": s, "analytics_rows": (ana.get("rows") if 'ana' in locals() else None)},
        )


# ─── TikTok ───────────────────────────────────────────────────────────────
class TikTokClient(OutcomeClient):
    """
    Uses TikTok Marketing API (for business accounts).
    Setup:
      1. Register at https://business.tiktok.com/portal/docs
      2. Get long-lived access token + advertiser_id
      3. Set env: TIKTOK_BUSINESS_TOKEN, TIKTOK_ADVERTISER_ID
    Personal accounts: TikTok Research API (different auth, separate quota).
    """
    platform = "tiktok"

    def __init__(self) -> None:
        self.token = os.environ.get("TIKTOK_BUSINESS_TOKEN", "")
        self.advertiser_id = os.environ.get("TIKTOK_ADVERTISER_ID", "")

    def fetch(self, video_id: str, feature_id: str = "") -> Outcome | None:
        if not self.token:
            devlog.append("outcome_skip", "supervisor", "feature", feature_id,
                          {"platform": "tiktok", "reason": "missing TIKTOK_BUSINESS_TOKEN",
                           "video_id": video_id})
            return None

        import requests
        try:
            r = requests.get(
                "https://business-api.tiktok.com/open_api/v1.3/video/insight/",
                headers={"Access-Token": self.token},
                params={"video_id": video_id, "advertiser_id": self.advertiser_id},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json().get("data", {}).get("insight", {})
        except Exception as e:
            devlog.append("outcome_error", "supervisor", "feature", feature_id,
                          {"platform": "tiktok", "error": str(e)})
            return None

        impressions = int(data.get("show_count", 0))
        avg_pct = float(data.get("avg_watch_pct", 0)) / 100 if data.get("avg_watch_pct") else None
        engagement = None
        if impressions:
            likes = int(data.get("like_count", 0))
            comments = int(data.get("comment_count", 0))
            shares = int(data.get("share_count", 0))
            engagement = (likes + comments + shares) / impressions

        return Outcome(
            platform="tiktok",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            impressions=impressions,
            watch_through_pct=avg_pct,
            avg_watch_s=data.get("avg_watch_time"),
            engagement_rate=engagement,
            raw=data,
        )


# ─── Meta (Instagram Reels / Facebook) ────────────────────────────────────
class MetaClient(OutcomeClient):
    """
    Uses Meta Marketing API for Instagram Reels / Facebook ad videos.
    Setup:
      1. https://developers.facebook.com/apps — create app
      2. Generate long-lived token for the page/ad-account
      3. Set env: META_AD_ACCESS_TOKEN, META_AD_ACCOUNT_ID
    """
    platform = "meta"

    def __init__(self) -> None:
        self.token = os.environ.get("META_AD_ACCESS_TOKEN", "")
        self.account_id = os.environ.get("META_AD_ACCOUNT_ID", "")

    def fetch(self, video_id: str, feature_id: str = "") -> Outcome | None:
        if not self.token:
            devlog.append("outcome_skip", "supervisor", "feature", feature_id,
                          {"platform": "meta", "reason": "missing META_AD_ACCESS_TOKEN",
                           "video_id": video_id})
            return None

        import requests
        try:
            r = requests.get(
                f"https://graph.facebook.com/v21.0/{video_id}/video_insights",
                params={
                    "access_token": self.token,
                    "metric": "total_video_impressions,total_video_avg_time_watched,"
                              "total_video_views_unique,total_video_complete_views_30s",
                },
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            devlog.append("outcome_error", "supervisor", "feature", feature_id,
                          {"platform": "meta", "error": str(e)})
            return None

        # Parse metrics by name
        m = {item["name"]: item["values"][0]["value"]
             for item in data.get("data", []) if item.get("values")}
        impressions = int(m.get("total_video_impressions", 0))
        avg_s = float(m.get("total_video_avg_time_watched", 0))

        return Outcome(
            platform="meta",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            impressions=impressions,
            avg_watch_s=avg_s,
            watch_through_pct=None,  # Meta doesn't expose directly; compute downstream
            raw=data,
        )


# ─── Manual ingest (CSV/JSON paste, no API) ──────────────────────────────
class ManualOutcomeClient(OutcomeClient):
    """
    Read outcomes from out/<feature_id>/outcome_manual.json.

    Useful when:
      - API setup not yet done
      - Client provides metrics from their own dashboard
      - One-off backfill from historical campaigns
    """
    platform = "manual"

    def fetch(self, video_id: str, feature_id: str = "") -> Outcome | None:
        path = Path("out") / feature_id / "outcome_manual.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            devlog.append("outcome_error", "supervisor", "feature", feature_id,
                          {"platform": "manual", "error": str(e)})
            return None
        return Outcome(
            platform=data.get("platform", "manual"),
            published_at=data.get("published_at"),
            fetched_at=datetime.now(timezone.utc).isoformat(),
            impressions=int(data.get("impressions", 0)),
            watch_through_pct=data.get("watch_through_pct"),
            avg_watch_s=data.get("avg_watch_s"),
            engagement_rate=data.get("engagement_rate"),
            ctr=data.get("ctr"),
            conversion_n=int(data.get("conversion_n", 0)),
            raw=data,
        )


# ─── Registry + public helper ────────────────────────────────────────────
CLIENTS: dict[str, type[OutcomeClient]] = {
    "youtube": YouTubeClient,
    "tiktok":  TikTokClient,
    "meta":    MetaClient,
    "manual":  ManualOutcomeClient,
}


def fetch_and_log(platform: str, video_id: str, feature_id: str) -> Outcome | None:
    """Fetch outcome for a feature on a platform; log to devlog if successful."""
    client_cls = CLIENTS.get(platform)
    if client_cls is None:
        devlog.append("outcome_error", "supervisor", "feature", feature_id,
                      {"platform": platform, "error": "unknown platform"})
        return None
    client = client_cls()
    outcome = client.fetch(video_id, feature_id=feature_id)
    if outcome is None:
        return None
    devlog.log_outcome(feature_id, platform, outcome.as_dict())
    return outcome
