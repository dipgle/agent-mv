#!/usr/bin/env python3
"""
Eval HTTP server — JSON endpoints for dashboard.html.

Pure-stdlib (http.server + sqlite3) so it works without flask/fastapi.
Read-only against logs/devlog.sqlite + its VIEWs.

Auth mode (set EVAL_SERVE_TOKEN):
  - Token missing in env → localhost-only mode (127.0.0.1 forced, no auth)
  - Token set in env    → any --host accepted; every request needs
                          Authorization: Bearer <token>
  - Public endpoints (no auth needed even when token is set):
    /health   — health check
    /         — dashboard HTML (JS fetches /eval/api/* separately with token)

Rate limit (token bucket, per IP, in-memory, resets on restart):
  - /eval/api/*: 100 req/min per IP when authenticated
  - Any endpoint, unauthenticated / localhost-only mode: 10 req/min per IP

CORS:
  - --cors-origin <origin> enables CORS for that origin
  - Sets Access-Control-Allow-Origin + Access-Control-Allow-Headers: Authorization
  - Handles OPTIONS preflight

Logging:
  - Every request appended to logs/eval_serve_access.log
  - Rotating: 10 MB cap, keep 3 backups
  - Format: [ISO8601] METHOD PATH STATUS LATENCY_MS BYTES IP TOKEN_HASH[:8]

Run:
    python eval/serve.py                          # localhost-only, :7891
    python eval/serve.py --port 8765
    EVAL_SERVE_TOKEN=s3cr3t \\
        python eval/serve.py --host 0.0.0.0       # LAN / internet-exposed

Endpoints:
    GET  /                                → eval/dashboard.html
    GET  /health                          → {ok, schema_version, uptime_s, requests_last_min}
    GET  /eval/api/cost/mtd               → month-to-date budget + burn rate
    GET  /eval/api/cost/top_spend         → top 10 videos by total spend
    GET  /eval/api/cost/spend_per_model
    GET  /eval/api/cost/per_modality_daily
    GET  /eval/api/cost/vs_outcome        → cost vs watch-through scatter
    GET  /eval/api/cost/forecast          → monthly + per-video cost forecast
    GET  /eval/api/proposals/pending
    GET  /eval/api/proposals/decisions
    GET  /eval/api/canaries/active
    GET  /eval/api/audit/latest           → latest audit_*.md parsed
    GET  /eval/api/eval/per_dimension?feature_id=
    GET  /eval/api/eval/recent            → last 50 eval_verdict events
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
import logging.handlers
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import date, datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ─── Paths and constants ─────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
DEVLOG = ROOT / "logs" / "devlog.sqlite"
DASHBOARD = ROOT / "eval" / "dashboard.html"
LOG_DIR = ROOT / "logs"
ACCESS_LOG = LOG_DIR / "eval_serve_access.log"

SCHEMA_VERSION = 2
MAX_PER_MONTH = float(os.environ.get("MAX_COST_PER_MONTH_USD", "500.00"))

# Public endpoints that never require auth even when EVAL_SERVE_TOKEN is set.
_PUBLIC_PATHS = frozenset({"/", "/dashboard.html", "/health"})

# Rate-limit buckets: {ip: deque of timestamps}
# Separate buckets for authenticated vs unauthenticated traffic.
_rl_lock = threading.Lock()
_rl_auth: dict[str, collections.deque] = {}    # 100 req/min
_rl_unauth: dict[str, collections.deque] = {}  # 10 req/min
_RL_AUTH_MAX = 100
_RL_UNAUTH_MAX = 10
_RL_WINDOW_S = 60

# Server start time for uptime reporting.
_SERVER_START = time.monotonic()

# Per-minute request counter for /health (rolling window, global).
_req_timestamps: collections.deque = collections.deque()
_req_ts_lock = threading.Lock()


# ─── Auth ────────────────────────────────────────────────────────────────────

def _token_from_env() -> str | None:
    """Return the bearer token from env, or None if not set."""
    t = os.environ.get("EVAL_SERVE_TOKEN", "").strip()
    return t if t else None


def _token_hash(token: str) -> str:
    """Return truncated SHA-256 for safe log reference (first 8 hex chars)."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def _check_auth(handler: "Handler", expected_token: str | None) -> bool:
    """
    Return True if the request is authorised.

    When expected_token is None (localhost-only mode), all requests pass.
    Public paths always pass.
    Otherwise validate the Authorization: Bearer <token> header.
    """
    parsed = urllib.parse.urlparse(handler.path)
    if parsed.path in _PUBLIC_PATHS:
        return True
    if expected_token is None:
        return True
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    return auth_header[7:].strip() == expected_token


# ─── Rate limiting ────────────────────────────────────────────────────────────

def _client_ip(handler: "Handler") -> str:
    """Best-effort client IP (respects X-Forwarded-For if present)."""
    xff = handler.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return handler.client_address[0]


def _check_rate_limit(ip: str, authenticated: bool) -> bool:
    """
    Token-bucket rate limit check.

    Returns True if the request is allowed; False if the limit is exceeded.
    Authenticated traffic gets 100 req/min; unauthenticated gets 10 req/min.
    In localhost-only mode (no token set) the high authenticated limit applies.
    """
    bucket = _rl_auth if authenticated else _rl_unauth
    max_req = _RL_AUTH_MAX if authenticated else _RL_UNAUTH_MAX
    now = time.monotonic()

    with _rl_lock:
        dq = bucket.setdefault(ip, collections.deque())
        # Drop timestamps older than the window.
        cutoff = now - _RL_WINDOW_S
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_req:
            return False
        dq.append(now)
        return True


# ─── Access logger ────────────────────────────────────────────────────────────

def _build_access_logger() -> logging.Logger:
    """Create a rotating file logger for access logging."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("eval_serve_access")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.handlers.RotatingFileHandler(
            ACCESS_LOG,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=3,
            encoding="utf-8",
        )
        h.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(h)
    return logger


_ACCESS_LOGGER = _build_access_logger()


def _log_access(
    method: str,
    path: str,
    status: int,
    latency_ms: float,
    nbytes: int,
    ip: str,
    token_hash: str,
) -> None:
    """Append one line to the rotating access log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    _ACCESS_LOGGER.info(
        "[%s] %s %s %d %.1f %d %s %s",
        ts, method, path, status, latency_ms, nbytes, ip, token_hash,
    )


# ─── SQLite helper ────────────────────────────────────────────────────────────

def _q(sql: str, params: tuple = ()) -> list[dict]:
    with sqlite3.connect(f"file:{DEVLOG}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, params).fetchall()]


# ─── Endpoint handlers ────────────────────────────────────────────────────────

def get_health() -> dict:
    """Public health endpoint — always unauthenticated."""
    uptime = time.monotonic() - _SERVER_START
    now = time.monotonic()
    with _req_ts_lock:
        cutoff = now - 60.0
        while _req_timestamps and _req_timestamps[0] < cutoff:
            _req_timestamps.popleft()
        last_min = len(_req_timestamps)
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "uptime_s": round(uptime, 1),
        "requests_last_min": last_min,
    }


def get_cost_mtd() -> dict:
    rows = _q("""
        SELECT COALESCE(SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)), 0) AS spent,
               COUNT(DISTINCT ref_id) AS videos,
               COUNT(*) AS calls
        FROM events
        WHERE kind='model_run'
          AND strftime('%Y-%m', ts) = strftime('%Y-%m','now')
    """)
    r = rows[0] if rows else {"spent": 0, "videos": 0, "calls": 0}
    spent = float(r["spent"])
    return {
        "spent_usd": round(spent, 4),
        "cap_usd": MAX_PER_MONTH,
        "remaining_usd": round(MAX_PER_MONTH - spent, 4),
        "burn_rate_pct": round(spent / MAX_PER_MONTH * 100 if MAX_PER_MONTH else 0, 2),
        "videos": int(r["videos"] or 0),
        "calls": int(r["calls"] or 0),
    }


def get_cost_top_spend() -> list[dict]:
    return _q("""
        SELECT
            ref_id AS feature_id,
            SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS total_usd,
            SUM(CAST(json_extract(content,'$.cost.cloud_usd') AS REAL)) AS cloud_usd,
            SUM(CAST(json_extract(content,'$.cost.compute_usd') AS REAL)) AS compute_usd,
            SUM(CAST(json_extract(content,'$.cost.electricity_usd') AS REAL)) AS elec_usd,
            COUNT(*) AS n_calls
        FROM events
        WHERE kind='model_run' AND ref_id != ''
          AND ts > datetime('now','-30 days')
        GROUP BY ref_id
        ORDER BY total_usd DESC
        LIMIT 10
    """)


def get_cost_spend_per_model() -> list[dict]:
    return _q("""
        SELECT
            json_extract(content,'$.model') AS model,
            json_extract(content,'$.tier') AS tier,
            COUNT(*) AS n,
            SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS total_usd,
            AVG(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS avg_usd
        FROM events
        WHERE kind='model_run'
          AND ts > datetime('now','-30 days')
        GROUP BY model, tier
        ORDER BY total_usd DESC
        LIMIT 15
    """)


def get_cost_per_modality_daily() -> list[dict]:
    return _q("""
        SELECT
            DATE(ts) AS day,
            json_extract(content,'$.modality') AS modality,
            COUNT(*) AS n,
            SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS total_usd
        FROM events
        WHERE kind='model_run'
          AND ts > datetime('now','-30 days')
        GROUP BY day, modality
        ORDER BY day DESC, total_usd DESC
        LIMIT 60
    """)


def get_cost_vs_outcome() -> list[dict]:
    return _q("""
        SELECT * FROM cost_vs_outcome
        WHERE total_usd IS NOT NULL
        LIMIT 100
    """)


def get_cost_forecast() -> dict:
    """
    Current cost forecast snapshot.

    Returns:
        monthly_projection_usd  — linear extrapolation of last 7d burn rate
        daily_rate_usd          — avg spend/day over the last 7 days
        per_video_moving_avg    — moving average of last 10 completed videos
        eta_days_to_cap         — days until monthly cap at current rate (None if no cap risk)
        mtd_spent_usd           — month-to-date spend
        cap_usd                 — configured monthly cap
    """
    # Daily burn rate over last 7 days.
    daily_rows = _q("""
        SELECT DATE(ts) AS day,
               SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS day_usd
        FROM events
        WHERE kind='model_run'
          AND ts > datetime('now', '-7 days')
        GROUP BY day
        ORDER BY day
    """)
    if daily_rows:
        daily_rate = sum(r["day_usd"] or 0 for r in daily_rows) / max(len(daily_rows), 1)
    else:
        daily_rate = 0.0

    # Days remaining in the current month.
    today = date.today()
    # Last day of current month.
    if today.month == 12:
        days_in_month = 31
    else:
        days_in_month = (date(today.year, today.month + 1, 1) - date(today.year, today.month, 1)).days
    days_remaining = days_in_month - today.day

    # MTD spend.
    mtd_rows = _q("""
        SELECT COALESCE(SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)), 0) AS spent
        FROM events
        WHERE kind='model_run'
          AND strftime('%Y-%m', ts) = strftime('%Y-%m','now')
    """)
    mtd_spent = float(mtd_rows[0]["spent"] if mtd_rows else 0)

    # Monthly projection: spent so far + remaining days * daily rate.
    monthly_projection = mtd_spent + days_remaining * daily_rate

    # Per-video moving average (last 10 completed videos by total cost).
    video_rows = _q("""
        SELECT ref_id,
               SUM(CAST(json_extract(content,'$.cost.total_usd') AS REAL)) AS total_usd
        FROM events
        WHERE kind='model_run' AND ref_id != ''
        GROUP BY ref_id
        ORDER BY MAX(ts) DESC
        LIMIT 10
    """)
    if video_rows:
        per_video_avg = sum(r["total_usd"] or 0 for r in video_rows) / len(video_rows)
    else:
        per_video_avg = 0.0

    # ETA to cap.
    remaining_budget = MAX_PER_MONTH - mtd_spent
    if daily_rate > 0 and remaining_budget > 0:
        eta_days = remaining_budget / daily_rate
    else:
        eta_days = None

    return {
        "monthly_projection_usd": round(monthly_projection, 4),
        "daily_rate_usd": round(daily_rate, 4),
        "per_video_moving_avg_usd": round(per_video_avg, 4),
        "eta_days_to_cap": round(eta_days, 1) if eta_days is not None else None,
        "mtd_spent_usd": round(mtd_spent, 4),
        "cap_usd": MAX_PER_MONTH,
        "days_remaining_in_month": days_remaining,
        "days_in_month": days_in_month,
    }


def get_proposals_pending() -> list[dict]:
    return _q("SELECT * FROM proposals_pending ORDER BY ts DESC LIMIT 30")


def get_proposal_decisions() -> list[dict]:
    return _q("""
        SELECT * FROM proposal_decisions
        WHERE decided_at > datetime('now','-30 days')
        ORDER BY decided_at DESC LIMIT 30
    """)


def get_canaries_active() -> list[dict]:
    return _q("""
        SELECT * FROM canaries
        WHERE verdict IN ('started','extend')
        ORDER BY ts DESC LIMIT 20
    """)


def get_audit_latest() -> dict:
    reports = sorted((ROOT / "eval" / "reports").glob("audit_*.md"))
    if not reports:
        return {"empty": True}
    latest = reports[-1]
    return {
        "path": str(latest.relative_to(ROOT)),
        "content_md": latest.read_text(encoding="utf-8"),
        "date": latest.stem.replace("audit_", ""),
    }


def get_eval_per_dimension(feature_id: str) -> list[dict]:
    return _q("""
        SELECT * FROM eval_tier_results
        WHERE feature_id = ? ORDER BY ts DESC
    """, (feature_id,))


def get_eval_recent() -> list[dict]:
    return _q("""
        SELECT ts, ref_id AS feature_id,
               json_extract(content,'$.verdict') AS verdict,
               CAST(json_extract(content,'$.tier2_overall_score') AS REAL) AS score,
               json_extract(content,'$.blocker_dimensions') AS blockers,
               json_extract(content,'$.panel_models') AS models
        FROM events
        WHERE kind='eval_verdict'
        ORDER BY ts DESC LIMIT 50
    """)


def get_outcomes_latest() -> list[dict]:
    return _q("""
        SELECT * FROM outcomes_latest
        ORDER BY fetched_at DESC LIMIT 50
    """)


def get_outcomes_summary() -> list[dict]:
    return _q("""
        SELECT * FROM outcomes_summary
        ORDER BY last_fetched DESC LIMIT 50
    """)


def get_champions() -> dict:
    p = ROOT / "eval" / "champions" / "index.json"
    if not p.exists():
        return {"empty": True}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        return {"error": str(e)}


def get_calibration_latest() -> dict:
    panel = _q("SELECT * FROM panel_calibrations ORDER BY ts DESC LIMIT 1")
    hook = _q("SELECT * FROM hook_calibrations ORDER BY ts DESC LIMIT 1")
    evolution = _q("SELECT * FROM champions_evolution ORDER BY ts DESC LIMIT 1")
    return {
        "panel": panel[0] if panel else None,
        "hook":  hook[0] if hook else None,
        "champions_evolution": evolution[0] if evolution else None,
    }


def get_panel_timeouts() -> list[dict]:
    return _q("""
        SELECT * FROM panel_timeouts
        WHERE day >= DATE('now', '-7 days')
        ORDER BY day DESC, timeout_count DESC
        LIMIT 50
    """)


def get_panel_breakers() -> list[dict]:
    return _q("SELECT * FROM panel_breaker_state")


def get_panel_partial() -> list[dict]:
    return _q("""
        SELECT * FROM panel_partial_count
        WHERE day >= DATE('now', '-7 days')
        ORDER BY day DESC
    """)


def get_moderation_recent() -> list[dict]:
    """Return the 50 most-recent moderation check events (any severity)."""
    return _q("""
        SELECT
            id, ts,
            ref_id AS feature_id,
            json_extract(content, '$.check')      AS check_name,
            CAST(json_extract(content, '$.flagged') AS INTEGER) AS flagged,
            json_extract(content, '$.severity')   AS severity,
            json_extract(content, '$.categories') AS categories_json,
            json_extract(content, '$.details')    AS details_json
        FROM events
        WHERE kind = 'moderation'
        ORDER BY ts DESC
        LIMIT 50
    """)


def get_c2pa_status() -> list[dict]:
    """Return C2PA embed status per feature (most-recent embed per feature)."""
    return _q("""
        SELECT
            ref_id AS feature_id,
            MAX(ts) AS last_embed_ts,
            MAX(CASE WHEN kind = 'c2pa_embedded' THEN 1 ELSE 0 END) AS embedded,
            MAX(CASE WHEN kind = 'c2pa_skipped'  THEN 1 ELSE 0 END) AS skipped,
            MAX(CASE WHEN kind = 'c2pa_error'    THEN 1 ELSE 0 END) AS error
        FROM events
        WHERE kind IN ('c2pa_embedded', 'c2pa_skipped', 'c2pa_error')
          AND ref_id != ''
        GROUP BY ref_id
        ORDER BY last_embed_ts DESC
        LIMIT 100
    """)


def get_trademark_index_size() -> dict:
    """Return count of logo images in data/trademark_index/."""
    tm_dir = ROOT / "data" / "trademark_index"
    if not tm_dir.exists():
        return {"count": 0, "path": str(tm_dir), "exists": False}
    count = sum(1 for f in tm_dir.iterdir()
                if f.suffix.lower() in (".png", ".jpg", ".jpeg"))
    return {"count": count, "path": str(tm_dir), "exists": True}


# ─── Route table ──────────────────────────────────────────────────────────────

ROUTES: dict[str, object] = {
    "/eval/api/cost/mtd":                get_cost_mtd,
    "/eval/api/cost/top_spend":          get_cost_top_spend,
    "/eval/api/cost/spend_per_model":    get_cost_spend_per_model,
    "/eval/api/cost/per_modality_daily": get_cost_per_modality_daily,
    "/eval/api/cost/vs_outcome":         get_cost_vs_outcome,
    "/eval/api/cost/forecast":           get_cost_forecast,
    "/eval/api/proposals/pending":       get_proposals_pending,
    "/eval/api/proposals/decisions":     get_proposal_decisions,
    "/eval/api/canaries/active":         get_canaries_active,
    "/eval/api/audit/latest":            get_audit_latest,
    "/eval/api/eval/recent":             get_eval_recent,
    "/eval/api/outcomes/latest":         get_outcomes_latest,
    "/eval/api/outcomes/summary":        get_outcomes_summary,
    "/eval/api/champions":               get_champions,
    "/eval/api/calibration/latest":      get_calibration_latest,
    "/eval/api/panel/timeouts":          get_panel_timeouts,
    "/eval/api/panel/breakers":          get_panel_breakers,
    "/eval/api/panel/partial":           get_panel_partial,
    # Compliance endpoints (added 2026-06-09)
    "/eval/api/moderation/recent":       get_moderation_recent,
    "/eval/api/c2pa/status":             get_c2pa_status,
    "/eval/api/compliance/trademark_size": get_trademark_index_size,
}


# ─── Request handler ──────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    # These two attributes are injected by main() after the class is defined.
    _auth_token: str | None = None
    _cors_origin: str | None = None

    def log_message(self, fmt, *args):
        # Suppress default stderr logging; access log handles it.
        pass

    # Track when the request started so latency can be computed in _finish().
    def handle(self):
        self._t0 = time.monotonic()
        super().handle()

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        if not self._cors_origin:
            self._send_plain(405, b"Method Not Allowed")
            return
        origin = self.headers.get("Origin", "")
        if origin != self._cors_origin:
            self._send_plain(403, b"Forbidden")
            return
        self.send_response(204)
        self._add_cors_headers(origin)
        self.send_header("Content-Length", "0")
        self.end_headers()
        self._record_request(204, 0)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        ip = _client_ip(self)
        token = self._auth_token

        # --- Auth check ---
        authed = _check_auth(self, token)
        # In localhost-only mode (no token), treat as authenticated for rate-limit.
        is_localhost_mode = token is None

        if not authed:
            body = json.dumps({"error": "Unauthorized"}).encode()
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="eval-dashboard"')
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._add_cors_headers(self.headers.get("Origin", ""))
            self.end_headers()
            self.wfile.write(body)
            self._record_request(401, len(body))
            return

        # --- Rate limit check ---
        use_auth_bucket = is_localhost_mode or authed
        if not _check_rate_limit(ip, authenticated=use_auth_bucket):
            body = json.dumps({"error": "Too Many Requests"}).encode()
            self.send_response(429)
            self.send_header("Retry-After", "60")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._add_cors_headers(self.headers.get("Origin", ""))
            self.end_headers()
            self.wfile.write(body)
            self._record_request(429, len(body))
            return

        # --- Route: health ---
        if path == "/health":
            self._serve_json(get_health())
            return

        # --- Route: static dashboard ---
        if path in ("/", "/dashboard.html"):
            self._serve_file(DASHBOARD, "text/html; charset=utf-8")
            return

        # --- Route: parametric ---
        if path == "/eval/api/eval/per_dimension":
            fid = qs.get("feature_id", [""])[0]
            self._serve_json(get_eval_per_dimension(fid))
            return

        # --- Fixed routes ---
        if path in ROUTES:
            try:
                data = ROUTES[path]()  # type: ignore[operator]
            except Exception as e:
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(data)
            return

        # --- Static fallback under eval/ ---
        if path.startswith("/eval/"):
            candidate = ROOT / path.lstrip("/")
            if candidate.is_file():
                ctype = (
                    "text/html; charset=utf-8"
                    if candidate.suffix == ".html"
                    else "application/octet-stream"
                )
                self._serve_file(candidate, ctype)
                return

        self._send_plain(404, b"404")

    def _add_cors_headers(self, request_origin: str) -> None:
        """Add CORS headers if the request origin matches the configured origin."""
        if self._cors_origin and request_origin == self._cors_origin:
            self.send_header("Access-Control-Allow-Origin", request_origin)
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def _serve_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._add_cors_headers(self.headers.get("Origin", ""))
        self.end_headers()
        self.wfile.write(body)
        self._record_request(status, len(body))

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self._send_plain(404, b"404")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._add_cors_headers(self.headers.get("Origin", ""))
        self.end_headers()
        self.wfile.write(body)
        self._record_request(200, len(body))

    def _send_plain(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._record_request(status, len(body))

    def _record_request(self, status: int, nbytes: int) -> None:
        """Log the completed request to the rotating access log."""
        t0 = getattr(self, "_t0", time.monotonic())
        latency_ms = (time.monotonic() - t0) * 1000

        # Track global request timestamps for /health.
        with _req_ts_lock:
            _req_timestamps.append(time.monotonic())
            cutoff = time.monotonic() - 60.0
            while _req_timestamps and _req_timestamps[0] < cutoff:
                _req_timestamps.popleft()

        # Compute token hash for log (safe — never logs the raw token).
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            raw = auth_header[7:].strip()
            th = _token_hash(raw) if raw else "none"
        else:
            th = "none"

        parsed_path = urllib.parse.urlparse(self.path).path
        _log_access(
            self.command or "?",
            parsed_path,
            status,
            latency_ms,
            nbytes,
            _client_ip(self),
            th,
        )


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Eval dashboard HTTP server")
    ap.add_argument("--host", default="127.0.0.1", help="Bind address")
    ap.add_argument("--port", type=int, default=7891, help="Listen port")
    ap.add_argument(
        "--cors-origin",
        default="",
        metavar="ORIGIN",
        help="Allowed CORS origin (e.g. https://dashboard.example.com). Empty = no CORS.",
    )
    args = ap.parse_args()

    if not DEVLOG.exists():
        print(f"DEVLOG missing: {DEVLOG} — run from project root.", file=sys.stderr)
        sys.exit(1)
    if not DASHBOARD.exists():
        print(f"Dashboard missing: {DASHBOARD}", file=sys.stderr)
        sys.exit(1)

    token = _token_from_env()

    # Enforce localhost-only when no token is set.
    if token is None:
        effective_host = "127.0.0.1"
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print(
                "WARNING: EVAL_SERVE_TOKEN is not set. "
                "Ignoring --host and binding to 127.0.0.1 (localhost-only mode).",
                file=sys.stderr,
            )
    else:
        effective_host = args.host

    # Inject runtime config into the handler class.
    Handler._auth_token = token
    Handler._cors_origin = args.cors_origin if args.cors_origin else None

    os.chdir(ROOT)  # Static file resolution assumes ROOT as CWD.

    mode = "localhost-only (no auth)" if token is None else "auth-required"
    print(
        f"Eval server on http://{effective_host}:{args.port}/  [{mode}]",
        file=sys.stderr,
    )
    if token is not None:
        print(
            f"  Token: set ({_token_hash(token)}...); "
            "pass Authorization: Bearer <token> on all /eval/api/* requests.",
            file=sys.stderr,
        )
    if Handler._cors_origin:
        print(f"  CORS origin: {Handler._cors_origin}", file=sys.stderr)
    print(f"  Access log: {ACCESS_LOG}", file=sys.stderr)

    HTTPServer((effective_host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "orchestrator"))
    try:
        from _console import ensure_utf8
        ensure_utf8()
    except ImportError:
        pass
    main()
