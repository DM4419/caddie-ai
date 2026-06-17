"""Browser-tier board: Product Builder Jobs (productbuilderjobs.vercel.app).

The site is a React SPA, but its listings come from a public Supabase/PostgREST
backend (the publishable key ships in the page bundle — it's meant to be public).
So instead of driving a headless browser, we hit that data API directly via a
"direct URL with filters applied", and paginate with limit/offset. PostgREST
returns the exact total in the `Content-Range` header, so two pages are enough
to know the full pattern (page size, total, how many pages remain).

If a future board has no reachable API, the browser tier would fall back to
Playwright DOM scraping — not needed here.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

import httpx

from adapters import dedupe
from engine.textutils import detect_role, normalize_mode

BASE = "https://soveiffwpugqmgdupoir.supabase.co/rest/v1/published_jobs"
# Public "publishable" key from the site's JS bundle (anon-equivalent, read-only).
KEY = "sb_publishable_jxh4QBA1H-7E0rfHKhv_7Q_X_q8ihpw"
COLS = "id,job_id,title,company,location,is_remote,job_type,date_posted,description,apply_url"

MAX_PAGE_SIZE = 50
MAX_PAGES = 10
DEPTH = "paginated"   # real page-based pagination; page_size + pages both apply


def _headers() -> dict:
    return {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Prefer": "count=exact"}


def _params(keyword: str, remote_only: bool, page_size: int, offset: int) -> dict:
    params = {
        "select": COLS,
        "order": "published_at.desc",
        "limit": str(page_size),
        "offset": str(offset),
    }
    if keyword.strip():
        params["title"] = f"ilike.*{keyword.strip()}*"
    if remote_only:
        params["is_remote"] = "eq.true"
    return params


def direct_url(keyword: str = "", remote_only: bool = False, page_size: int = 20) -> str:
    """The filters-applied data URL — what the tool actually fetches (page 1)."""
    return f"{BASE}?{urlencode(_params(keyword, remote_only, page_size, 0))}"


def _normalize(row: dict) -> dict:
    title = row.get("title") or ""
    loc = row.get("location") or ""
    # the board's is_remote flag is unreliable; a "remote" job tied to a specific
    # city is usually hybrid/remote-friendly, so don't overstate full remote.
    has_city = bool(loc.strip()) and not re.search(
        r"\b(remote|anywhere|worldwide|global|distributed)\b", loc, re.I)
    if row.get("is_remote"):
        mode = "hybrid" if has_city else "remote"
    else:
        mode = normalize_mode("", f"{loc}\n{row.get('description', '')}")
    return {
        "role": detect_role(title),
        "title": title,
        "company": row.get("company") or "Unknown",
        "url": row.get("apply_url") or "",
        "mode": mode,
        "location": loc,
        "description": f"{title}\n{loc}\n\n{row.get('description', '') or ''}".strip(),
        "posted": (row.get("date_posted") or "")[:10],
        "source": "Product Builder Jobs",
    }


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2, since: str = None) -> dict:
    """Fetch with filters applied; dedupe by apply URL.

    If `since` (YYYY-MM-DD) is given, paginate through ALL pages until rows are
    older than `since` (the board lists newest-first) — so we get every job in
    the window. Otherwise fetch up to `pages` pages.
    Returns {total, pages_fetched, page_size, direct_url, jobs:[normalized]}.
    """
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    hard_max = 30 if since else max(1, min(int(pages), MAX_PAGES))
    jobs: list = []
    total = None
    fetched = 0

    for p in range(hard_max):
        offset = p * page_size
        r = httpx.get(BASE, headers=_headers(),
                      params=_params(keyword, remote_only, page_size, offset), timeout=30.0)
        r.raise_for_status()
        cr = r.headers.get("content-range", "")     # e.g. "0-19/126"
        if "/" in cr:
            tail = cr.split("/")[-1]
            if tail.isdigit():
                total = int(tail)
        rows = r.json()
        fetched += 1
        jobs.extend(_normalize(x) for x in rows)
        if not rows or (total is not None and offset + page_size >= total):
            break
        if since and rows and (rows[-1].get("date_posted") or "")[:10] < since:
            break                                   # reached jobs older than the window

    uniq = dedupe(jobs)
    return {
        "total": total if total is not None else len(uniq),
        "pages_fetched": fetched,
        "page_size": page_size,
        "direct_url": direct_url(keyword, remote_only, page_size),
        "jobs": uniq,
    }
