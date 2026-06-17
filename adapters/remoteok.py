"""API-tier board: RemoteOK (https://remoteok.com/api).

A single public JSON feed (~100 latest remote jobs, no server-side filtering or
pagination — the first array element is a legal notice). We fetch the feed once,
filter by keyword client-side, and cap the volume to page_size * pages.
"""
from __future__ import annotations

import httpx

from adapters import dedupe
from engine.textutils import UA, detect_role, fmt_salary, strip_html

FEED = "https://remoteok.com/api"
MAX_PAGE_SIZE = 50
MAX_PAGES = 10


def _normalize(j: dict) -> dict:
    title = j.get("position") or ""
    tags = ", ".join(j.get("tags") or [])
    loc = j.get("location") or "Remote"
    desc = strip_html(j.get("description") or "")
    return {
        "role": detect_role(title),
        "title": title,
        "company": j.get("company") or "Unknown",
        "url": j.get("url") or j.get("apply_url") or "",
        "mode": "remote",                      # RemoteOK is remote by definition
        "location": loc,
        "salary": fmt_salary(j.get("salary_min"), j.get("salary_max"), "USD"),
        "description": f"{title}\n{loc}\nTags: {tags}\n\n{desc}".strip(),
        "posted": (j.get("date") or "")[:10],
        "source": "RemoteOK",
    }


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2, since: str = None) -> dict:
    r = httpx.get(FEED, headers={"User-Agent": UA}, timeout=30.0)
    r.raise_for_status()
    items = [x for x in r.json() if isinstance(x, dict) and x.get("position")]

    kw = keyword.strip().lower()
    if kw:
        items = [x for x in items if kw in (
            (x.get("position") or "") + " " + " ".join(x.get("tags") or [])
            + " " + (x.get("description") or "")).lower()]

    jobs = dedupe([_normalize(x) for x in items])
    if since:                                   # keep the whole window (unknown-date kept)
        jobs = [j for j in jobs if not j["posted"] or j["posted"] >= since]
        total = len(jobs)
    else:
        total = len(jobs)
        cap = max(1, min(int(page_size), MAX_PAGE_SIZE)) * max(1, min(int(pages), MAX_PAGES))
        jobs = jobs[:cap]
    return {
        "total": total,
        "pages_fetched": 1,                    # single feed, no pagination
        "page_size": page_size,
        "direct_url": FEED,
        "jobs": jobs,
    }
