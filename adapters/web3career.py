"""API-tier board: Web3 Career (https://web3.career/web3-jobs-api).

Token-authenticated JSON API. The response is a 3-element array:
[title_str, usage_str, [job, job, ...]]. Supports real server-side pagination
via `page` plus `limit`, and a `remote=true` filter. Token comes from
WEB3_CAREER_TOKEN in .env (kept out of the UI-visible direct URL).
"""
from __future__ import annotations

import email.utils
import os

import httpx
from dotenv import load_dotenv

from adapters import dedupe
from engine.textutils import UA, detect_role, fmt_salary, normalize_mode, strip_html

load_dotenv()

API = "https://web3.career/api/v1"
MAX_PAGE_SIZE = 100
MAX_PAGES = 10
DEPTH = "paginated"   # real page-based pagination; page_size + pages both apply


def _posted(date_str: str) -> str:
    try:
        return email.utils.parsedate_to_datetime(date_str).date().isoformat()
    except Exception:
        return (date_str or "")[:10]


def _extract_jobs(payload) -> list:
    """The job list is the last array element of the response."""
    if isinstance(payload, list):
        for part in reversed(payload):
            if isinstance(part, list):
                return part
    return []


def _normalize(j: dict) -> dict:
    title = j.get("title") or ""
    loc = j.get("location") or ""
    raw_tags = j.get("tags")
    tags = ", ".join(raw_tags) if isinstance(raw_tags, list) else (raw_tags or "")
    desc = strip_html(j.get("description") or "")
    return {
        "role": detect_role(title),
        "title": title,
        "company": j.get("company") or "Unknown",
        "url": j.get("apply_url") or "",
        "mode": normalize_mode("remote" if j.get("is_remote") else "", f"{loc}\n{desc}"),
        "location": loc.strip(),
        "salary": fmt_salary(j.get("salary_min_value"), j.get("salary_max_value"),
                             j.get("salary_currency")),
        "description": f"{title}\n{loc}\nTags: {tags}\n\n{desc}".strip(),
        "posted": _posted(j.get("date")),
        "source": "Web3 Career",
    }


def _direct_url(page_size: int, remote_only: bool) -> str:
    parts = [f"limit={page_size}", "page=1"]
    if remote_only:
        parts.append("remote=true")
    parts.append("token=***")              # token masked in the UI
    return f"{API}?{'&'.join(parts)}"


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2, since: str = None) -> dict:
    token = os.environ.get("WEB3_CAREER_TOKEN")
    if not token:
        raise RuntimeError("WEB3_CAREER_TOKEN not set in .env")
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    hard_max = 10 if since else max(1, min(int(pages), MAX_PAGES))

    collected, fetched = [], 0
    for p in range(hard_max):
        # tag=product-manager filters to product roles server-side (the board is
        # general crypto/web3; without this it returns mostly engineering roles)
        params = {"token": token, "limit": str(page_size), "page": str(p + 1),
                  "tag": "product-manager"}
        if remote_only:
            params["remote"] = "true"
        r = httpx.get(API, headers={"User-Agent": UA}, params=params, timeout=30.0)
        r.raise_for_status()
        jobs = _extract_jobs(r.json())
        fetched += 1
        if not jobs:
            break
        collected.extend(jobs)
        if len(jobs) < page_size:
            break
        if since and _posted(jobs[-1].get("date")) < since:
            break                                   # reached jobs older than the window

    norm = [_normalize(x) for x in collected]
    kw = keyword.strip().lower()
    if kw:
        norm = [j for j in norm if kw in (j["title"] + " " + j["description"]).lower()]
    norm = dedupe(norm)
    return {
        "total": len(norm),
        "pages_fetched": fetched,
        "page_size": page_size,
        "direct_url": _direct_url(page_size, remote_only),
        "jobs": norm,
    }
