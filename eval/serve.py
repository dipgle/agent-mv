#!/usr/bin/env python3
"""
Eval HTTP server — JSON endpoints for dashboard.html.

Pure-stdlib (http.server + sqlite3) so it works without flask/fastapi.
Read-only against logs/devlog.sqlite + its VIEWs.  Same-origin with the
static HTML so no CORS concerns when both served by this process.

Run:
    python eval/serve.py                # serves on :7891
    python eval/serve.py --port 8765
    python eval/serve.py --host 0.0.0.0 # bind all interfaces (LAN)

Endpoints:
    GET  /                          → eval/dashboard.html
    GET  /eval/api/cost/mtd         → month-to-date budget + burn rate
    GET  /eval/api/cost/top_spend   → top 10 videos by total spend
    GET  /eval/api/cost/spend_per_model
    GET  /eval/api/cost/per_modality_daily
    GET  /eval/api/cost/vs_outcome  → cost vs watch-through scatter
    GET  /eval/api/proposals/pending
    GET  /eval/api/proposals/decisions
    GET  /eval/api/canaries/active
    GET  /eval/api/audit/latest     → latest audit_*.md parsed
    GET  /eval/api/eval/per_dimension?feature_id=
    GET  /eval/api/eval/recent      → last 50 eval_verdict events
"""

from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
from datetime import date
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVLOG = ROOT / "logs" / "devlog.sqlite"
DASHBOARD = ROOT / "eval" / "dashboard.html"
MAX_PER_MONTH = float(os.environ.get("MAX_COST_PER_MONTH_USD", "500.00"))


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with sqlite3.connect(f"file:{DEVLOG}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, params).fetchall()]


# ─── Endpoint handlers ───────────────────────────────────────────────────
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


ROUTES = {
    "/eval/api/cost/mtd":               get_cost_mtd,
    "/eval/api/cost/top_spend":         get_cost_top_spend,
    "/eval/api/cost/spend_per_model":   get_cost_spend_per_model,
    "/eval/api/cost/per_modality_daily": get_cost_per_modality_daily,
    "/eval/api/cost/vs_outcome":        get_cost_vs_outcome,
    "/eval/api/proposals/pending":      get_proposals_pending,
    "/eval/api/proposals/decisions":    get_proposal_decisions,
    "/eval/api/canaries/active":        get_canaries_active,
    "/eval/api/audit/latest":           get_audit_latest,
    "/eval/api/eval/recent":            get_eval_recent,
    "/eval/api/outcomes/latest":        get_outcomes_latest,
    "/eval/api/outcomes/summary":       get_outcomes_summary,
    "/eval/api/champions":              get_champions,
    "/eval/api/calibration/latest":     get_calibration_latest,
}


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if os.environ.get("SERVE_VERBOSE"):
            super().log_message(fmt, *args)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # Static dashboard
        if path in ("/", "/dashboard.html"):
            self._serve_file(DASHBOARD, "text/html; charset=utf-8")
            return

        # Parametric: per-dimension by feature_id
        if path == "/eval/api/eval/per_dimension":
            fid = qs.get("feature_id", [""])[0]
            data = get_eval_per_dimension(fid)
            self._serve_json(data)
            return

        # Fixed routes
        if path in ROUTES:
            try:
                data = ROUTES[path]()
            except Exception as e:
                self._serve_json({"error": str(e)}, status=500)
                return
            self._serve_json(data)
            return

        # Fallback: try static under eval/
        if path.startswith("/eval/"):
            candidate = ROOT / path.lstrip("/")
            if candidate.is_file():
                ctype = "text/html; charset=utf-8" if candidate.suffix == ".html" else "application/octet-stream"
                self._serve_file(candidate, ctype)
                return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404")

    def _serve_json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path, content_type: str):
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7891)
    args = ap.parse_args()

    if not DEVLOG.exists():
        print(f"DEVLOG missing: {DEVLOG} — run from project root.")
        sys.exit(1)
    if not DASHBOARD.exists():
        print(f"Dashboard missing: {DASHBOARD}")
        sys.exit(1)

    os.chdir(ROOT)  # important so static files resolve

    print(f"Eval server on http://{args.host}:{args.port}/")
    print(f"Open: http://{args.host}:{args.port}/")
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "orchestrator"))
    try:
        from _console import ensure_utf8
        ensure_utf8()
    except ImportError:
        pass
    main()
