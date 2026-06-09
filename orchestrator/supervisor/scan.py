#!/usr/bin/env python3
"""
Supervisor Job B — External scan (weekly cron).

Sources scanned:
  1. HuggingFace trending (text-to-image, image-to-video, TTS, music)
  2. arxiv recent (cs.CV, cs.SD) filtered by efficiency/distillation keywords
  3. LiteLLM pricing changes (model_prices.json diff vs cached)
  4. ComfyUI Manager new custom nodes
  5. OpenRouter free tier changes
  6. Competitor release notes (Runway/Pika/Sora)

Every consulted URL is logged via devlog.log_source() per Question Discipline
rule. Findings are written to eval/benchmarks/external_sources.json and as
events kind='external_finding'.
"""

from __future__ import annotations
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import devlog  # noqa: E402

BENCHMARKS = Path("eval/benchmarks")
REPORTS = Path("eval/reports")

# Ratelimit-aware retry helper.
MAX_RETRIES = 2
RETRY_DELAYS = [2, 8]  # exponential backoff: 2s, 8s


# ─── 1. HuggingFace trending ──────────────────────────────────────────────
def scan_hf_trending(categories: list[str]) -> list[dict]:
    """Use HuggingFace API to list trending models per category."""
    findings = []
    for cat in categories:
        url = (f"https://huggingface.co/api/models?"
               f"{urlencode({'pipeline_tag': cat, 'sort': 'trendingScore', 'limit': 10})}")
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            items = r.json()
        except Exception as e:
            devlog.append("scan_error", "supervisor", "system", "hf",
                          {"category": cat, "error": str(e)})
            continue

        for it in items:
            model_id = it.get("modelId") or it.get("id")
            if not model_id:
                continue
            findings.append({
                "source": "huggingface",
                "category": cat,
                "model_id": model_id,
                "downloads_24h": it.get("downloads", 0),
                "likes": it.get("likes", 0),
                "url": f"https://huggingface.co/{model_id}",
            })
        devlog.log_source(
            "supervisor",
            url=url,
            takeaway=f"HF trending {cat}: top {len(items)} models",
        )
        time.sleep(1)  # be polite
    return findings


# ─── 2. arxiv recent papers ───────────────────────────────────────────────
def scan_arxiv(days: int = 7) -> list[dict]:
    """Pull recent arxiv papers in cs.CV / cs.SD with efficiency keywords."""
    keywords = [
        "distillation", "quantization", "efficient", "fast",
        "real-time", "low-cost", "few-step",
    ]
    findings = []
    for kw in keywords:
        url = (f"http://export.arxiv.org/api/query?"
               f"search_query=cat:cs.CV+AND+abs:{kw}&start=0&max_results=10"
               f"&sortBy=submittedDate&sortOrder=descending")
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            devlog.append("scan_error", "supervisor", "system", "arxiv",
                          {"keyword": kw, "error": str(e)})
            continue
        # Parse very loosely (Atom XML); avoid feedparser dep
        import re
        entries = re.findall(r"<entry>(.*?)</entry>", r.text, re.DOTALL)
        for e in entries[:5]:
            title = re.search(r"<title>(.*?)</title>", e, re.DOTALL)
            id_ = re.search(r"<id>(.*?)</id>", e, re.DOTALL)
            summary = re.search(r"<summary>(.*?)</summary>", e, re.DOTALL)
            if title and id_:
                findings.append({
                    "source": "arxiv",
                    "keyword": kw,
                    "title": title.group(1).strip(),
                    "url": id_.group(1).strip(),
                    "summary": (summary.group(1).strip()[:300] + "...")
                                if summary else "",
                })
        devlog.log_source(
            "supervisor",
            url=url,
            takeaway=f"arxiv kw='{kw}': {len(entries)} recent",
        )
        time.sleep(2)  # arxiv ratelimit
    return findings


# ─── 3. LiteLLM pricing changes ───────────────────────────────────────────
PRICING_URL = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
               "model_prices_and_context_window.json")


def scan_pricing() -> list[dict]:
    """Pull canonical model price list, diff vs cached snapshot."""
    cache_path = BENCHMARKS / "litellm_prices_cached.json"
    try:
        r = requests.get(PRICING_URL, timeout=30)
        r.raise_for_status()
        current = r.json()
    except Exception as e:
        devlog.append("scan_error", "supervisor", "system", "pricing",
                      {"error": str(e)})
        return []

    devlog.log_source(
        "supervisor",
        url=PRICING_URL,
        takeaway=f"LiteLLM prices: {len(current)} entries",
    )

    findings = []
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        for model, info in current.items():
            old = cached.get(model)
            new_in = info.get("input_cost_per_token")
            new_out = info.get("output_cost_per_token")
            if old is None:
                findings.append({
                    "source": "litellm-prices",
                    "model": model,
                    "change": "added",
                    "input_cost_per_token": new_in,
                    "output_cost_per_token": new_out,
                })
                continue
            old_in = old.get("input_cost_per_token")
            old_out = old.get("output_cost_per_token")
            if new_in != old_in or new_out != old_out:
                findings.append({
                    "source": "litellm-prices",
                    "model": model,
                    "change": "modified",
                    "old_input": old_in, "new_input": new_in,
                    "old_output": old_out, "new_output": new_out,
                })

    BENCHMARKS.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(current, indent=2))
    return findings


# ─── 4. ComfyUI custom nodes ──────────────────────────────────────────────
CUSTOM_NODES_URL = ("https://raw.githubusercontent.com/ltdrdata/"
                    "ComfyUI-Manager/main/custom-node-list.json")


def scan_comfy_nodes() -> list[dict]:
    """Diff ComfyUI Manager custom node list vs cached snapshot."""
    cache_path = BENCHMARKS / "comfy_nodes_cached.json"
    try:
        r = requests.get(CUSTOM_NODES_URL, timeout=30)
        r.raise_for_status()
        current_list = r.json().get("custom_nodes", [])
    except Exception as e:
        devlog.append("scan_error", "supervisor", "system", "comfy_nodes",
                      {"error": str(e)})
        return []

    devlog.log_source(
        "supervisor",
        url=CUSTOM_NODES_URL,
        takeaway=f"ComfyUI nodes: {len(current_list)} entries",
    )

    current_ids = {n.get("reference") or n.get("id") or n["title"]: n
                   for n in current_list}
    findings = []
    if cache_path.exists():
        cached_ids = set(json.loads(cache_path.read_text()).get("ids", []))
        new_ids = set(current_ids) - cached_ids
        for nid in list(new_ids)[:20]:
            n = current_ids[nid]
            findings.append({
                "source": "comfy-manager",
                "title": n.get("title"),
                "reference": n.get("reference"),
                "description": (n.get("description") or "")[:200],
                "url": n.get("reference"),
            })

    BENCHMARKS.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"ids": list(current_ids)}, indent=2))
    return findings


# ─── 5. Civitai trending ─────────────────────────────────────────────────
def scan_civitai_trending() -> list[dict]:
    """Query Civitai API for trending LoRAs and checkpoints."""
    findings = []
    types = ["LORA", "Checkpoint"]

    for model_type in types:
        params = {
            'sort': 'Most Downloaded',
            'period': 'Week',
            'type': model_type,
            'limit': 20
        }
        url = f"https://civitai.com/api/v1/models?{urlencode(params)}"

        for retry in range(MAX_RETRIES + 1):
            try:
                r = requests.get(url, timeout=20)
                if r.status_code == 429 or r.status_code == 503:
                    if retry < MAX_RETRIES:
                        delay = RETRY_DELAYS[retry]
                        time.sleep(delay)
                        continue
                    else:
                        raise Exception(f"Rate limited after {MAX_RETRIES} retries")
                r.raise_for_status()
                data = r.json()
                items = data.get("items", [])

                for item in items:
                    findings.append({
                        "source": "civitai",
                        "name": item.get("name", ""),
                        "type": model_type,
                        "downloads": item.get("stats", {}).get("downloadCount", 0),
                        "base_model": item.get("modelVersions", [{}])[0].get("baseModel", "")
                        if item.get("modelVersions") else "",
                        "url": f"https://civitai.com/models/{item.get('id', '')}",
                    })

                devlog.log_source(
                    "supervisor",
                    url=url,
                    takeaway=f"Civitai {model_type}: {len(items)} trending",
                )
                break  # success
            except Exception as e:
                if retry == MAX_RETRIES:
                    devlog.append("scan_error", "supervisor", "system", "civitai",
                                  {"type": model_type, "error": str(e)})
                    break

        time.sleep(1)  # be polite

    return findings


# ─── 6. Replicate trending ───────────────────────────────────────────────
def scan_replicate_trending() -> list[dict]:
    """Query Replicate explore page for trending video/audio models."""
    findings = []
    url = "https://replicate.com/explore"

    for retry in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (agent-mv supervisor scan)"
            })
            if r.status_code == 429 or r.status_code == 503:
                if retry < MAX_RETRIES:
                    delay = RETRY_DELAYS[retry]
                    time.sleep(delay)
                    continue
                else:
                    raise Exception(f"Rate limited after {MAX_RETRIES} retries")
            r.raise_for_status()

            # Extract model slugs from HTML (basic pattern: /model-creator/model-name)
            pattern = r'href="(/[a-z0-9-]+/[a-z0-9-]+)"'
            matches = re.findall(pattern, r.text)

            for match in list(set(matches))[:20]:  # dedup and limit
                slug = match.strip("/")
                findings.append({
                    "source": "replicate",
                    "slug": slug,
                    "category": "video_audio",
                    "url": f"https://replicate.com{match}",
                })

            devlog.log_source(
                "supervisor",
                url=url,
                takeaway=f"Replicate trending: {len(matches)} models extracted",
            )
            break  # success
        except Exception as e:
            if retry == MAX_RETRIES:
                devlog.append("scan_error", "supervisor", "system", "replicate",
                              {"error": str(e)})
                break

    return findings


# ─── 7. GitHub trending ML ───────────────────────────────────────────────
def scan_github_trending_ml() -> list[dict]:
    """Query GitHub API for trending ML/video/audio repos."""
    findings = []
    topics = [
        "video-generation", "text-to-video", "image-to-video",
        "tts", "audio-generation", "comfyui"
    ]

    # Calculate date 7 days ago for pushed:> filter
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    for topic in topics:
        query = f"topic:{topic} pushed:>{week_ago}"
        params = {
            'q': query,
            'sort': 'stars',
            'order': 'desc',
            'per_page': 20
        }
        url = f"https://api.github.com/search/repositories?{urlencode(params)}"

        for retry in range(MAX_RETRIES + 1):
            try:
                r = requests.get(url, timeout=20)
                if r.status_code == 429 or r.status_code == 503:
                    if retry < MAX_RETRIES:
                        delay = RETRY_DELAYS[retry]
                        time.sleep(delay)
                        continue
                    else:
                        raise Exception(f"Rate limited after {MAX_RETRIES} retries")
                r.raise_for_status()
                data = r.json()
                items = data.get("items", [])

                for item in items:
                    findings.append({
                        "source": "github",
                        "repo": item.get("full_name", ""),
                        "stars_week": item.get("stargazers_count", 0),
                        "description": item.get("description", "")[:200] or "",
                        "topics": item.get("topics", []),
                        "url": item.get("html_url", ""),
                    })

                devlog.log_source(
                    "supervisor",
                    url=url,
                    takeaway=f"GitHub topic={topic}: {len(items)} repos",
                )
                break  # success
            except Exception as e:
                if retry == MAX_RETRIES:
                    devlog.append("scan_error", "supervisor", "system", "github",
                                  {"topic": topic, "error": str(e)})
                    break

        time.sleep(1)  # be polite with GitHub API

    return findings


# ─── 8. Reddit r/StableDiffusion ─────────────────────────────────────────
def scan_reddit_stable_diffusion() -> list[dict]:
    """Scan top posts from r/StableDiffusion this week."""
    findings = []
    url = "https://www.reddit.com/r/StableDiffusion/top.json"

    params = {"t": "week", "limit": 20}
    full_url = f"{url}?{urlencode(params)}"

    for retry in range(MAX_RETRIES + 1):
        try:
            r = requests.get(full_url, timeout=20, headers={
                "User-Agent": "agent-mv supervisor scan (+github.com/dipgle/agent-mv)"
            })
            if r.status_code == 429 or r.status_code == 503:
                if retry < MAX_RETRIES:
                    delay = RETRY_DELAYS[retry]
                    time.sleep(delay)
                    continue
                else:
                    raise Exception(f"Rate limited after {MAX_RETRIES} retries")
            r.raise_for_status()

            data = r.json()
            posts = data.get("data", {}).get("children", [])

            # Keywords that suggest a release/announcement.
            release_keywords = ["release", "announce", "v2", "v3", "new", "launch"]

            for post_wrapper in posts:
                post = post_wrapper.get("data", {})
                title = post.get("title", "").lower()

                # Filter by heuristic: title contains release-like keywords.
                is_release = any(kw in title for kw in release_keywords)

                if is_release:
                    findings.append({
                        "source": "reddit",
                        "title": post.get("title", ""),
                        "score": post.get("score", 0),
                        "url": f"https://reddit.com{post.get('permalink', '')}",
                        "flair": post.get("link_flair_text", ""),
                    })

            devlog.log_source(
                "supervisor",
                url=full_url,
                takeaway=f"Reddit r/StableDiffusion: {len(findings)} release posts",
            )
            break  # success
        except Exception as e:
            if retry == MAX_RETRIES:
                devlog.append("scan_error", "supervisor", "system", "reddit",
                              {"error": str(e)})
                break

    return findings


# ─── Aggregator ──────────────────────────────────────────────────────────
def main():
    BENCHMARKS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    all_findings = {
        "hf_trending": scan_hf_trending([
            "text-to-image", "image-to-video",
            "text-to-speech", "text-to-audio",
        ]),
        "arxiv": scan_arxiv(),
        "pricing_changes": scan_pricing(),
        "comfy_nodes_new": scan_comfy_nodes(),
        "civitai_trending": scan_civitai_trending(),
        "replicate_trending": scan_replicate_trending(),
        "github_trending_ml": scan_github_trending_ml(),
        "reddit_stablediffusion": scan_reddit_stable_diffusion(),
    }

    # Persist for propose.py to consume
    out_path = BENCHMARKS / f"external_sources_{today}.json"
    out_path.write_text(json.dumps(all_findings, indent=2, ensure_ascii=False))

    # Markdown report
    report_path = REPORTS / f"scan_{today}.md"
    parts = [f"# External scan — {today}", ""]
    for section, items in all_findings.items():
        parts.append(f"## {section}  (n={len(items)})\n")
        for it in items[:10]:
            parts.append(f"- {json.dumps(it, ensure_ascii=False)[:300]}")
        parts.append("")
    report_path.write_text("\n".join(parts))

    devlog.append(
        kind="scan_summary",
        actor="supervisor",
        ref_type="system",
        ref_id=today,
        content={
            "report_path": str(report_path),
            "data_path": str(out_path),
            "hf_n": len(all_findings["hf_trending"]),
            "arxiv_n": len(all_findings["arxiv"]),
            "pricing_changes_n": len(all_findings["pricing_changes"]),
            "comfy_new_n": len(all_findings["comfy_nodes_new"]),
            "civitai_n": len(all_findings["civitai_trending"]),
            "replicate_n": len(all_findings["replicate_trending"]),
            "github_n": len(all_findings["github_trending_ml"]),
            "reddit_n": len(all_findings["reddit_stablediffusion"]),
        },
    )

    print(f"Scan report:    {report_path}")
    print(f"Scan data:      {out_path}")
    print(f"  HF trending:  {len(all_findings['hf_trending'])}")
    print(f"  arxiv:        {len(all_findings['arxiv'])}")
    print(f"  pricing chg:  {len(all_findings['pricing_changes'])}")
    print(f"  comfy new:    {len(all_findings['comfy_nodes_new'])}")
    print(f"  civitai:      {len(all_findings['civitai_trending'])}")
    print(f"  replicate:    {len(all_findings['replicate_trending'])}")
    print(f"  github:       {len(all_findings['github_trending_ml'])}")
    print(f"  reddit:       {len(all_findings['reddit_stablediffusion'])}")


if __name__ == "__main__":
    from _console import ensure_utf8
    ensure_utf8()
    main()
