"""API-tier board: Adzuna (https://developer.adzuna.com).

Aggregator REST API, app_id + app_key auth (both from .env). It's a general job
board, so we search with `what` (defaults to "product manager"). Real path-based
pagination via /search/{page}; `count` is the server-side total.

NOTE: the API returns only a ~500-char description snippet, not the full JD — fine
for scoring, but drafts off it will be thin until the full posting is opened.
"""
from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from dotenv import load_dotenv

from adapters import dedupe
from engine.textutils import UA, detect_mode, detect_role, fmt_salary, strip_html

load_dotenv()

COUNTRY = "gb"                              # UK board (user is remote/UK-hybrid)
MAX_PAGE_SIZE = 50
MAX_PAGES = 10
DEPTH = "paginated"   # real page-based pagination; page_size + pages both apply
QUERY_BASED = True                          # general board: fan out over role_queries


def _clean_url(url: str) -> str:
    """Strip Adzuna's utm_* attribution params (one carries the app_id) but keep
    the `se`/`v` tokens the redirect needs."""
    try:
        p = urlparse(url)
        q = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_")]
        return urlunparse(p._replace(query=urlencode(q)))
    except Exception:
        return url


def _normalize(j: dict) -> dict:
    title = j.get("title") or ""
    company = (j.get("company") or {}).get("display_name") or "Unknown"
    loc = (j.get("location") or {}).get("display_name") or ""
    desc = strip_html(j.get("description") or "")
    return {
        "role": detect_role(title),
        "title": title,
        "company": company,
        "url": _clean_url(j.get("redirect_url") or ""),
        "mode": detect_mode(f"{loc}\n{desc}"),
        "location": loc,
        "salary": fmt_salary(j.get("salary_min"), j.get("salary_max"), "GBP",
                             bool(j.get("salary_is_predicted") in ("1", 1, True))),
        "description": f"{title}\n{loc}\n\n{desc}".strip(),
        "posted": (j.get("created") or "")[:10],
        "source": "Adzuna",
    }


def _direct_url(what: str, page_size: int) -> str:
    q = urlencode({"what": what, "results_per_page": page_size,
                   "app_id": "***", "app_key": "***"})
    return f"https://api.adzuna.com/v1/api/jobs/{COUNTRY}/search/1?{q}"


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2, since: str = None) -> dict:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set in .env")
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    hard_max = 8 if since else max(1, min(int(pages), MAX_PAGES))
    what = (keyword.strip() or "product manager") + (" remote" if remote_only else "")

    collected, total, fetched = [], None, 0
    for p in range(hard_max):
        params = {"app_id": app_id, "app_key": app_key, "what": what,
                  "results_per_page": str(page_size), "content-type": "application/json"}
        if since:
            params["sort_by"] = "date"               # newest first so since-break works
        r = httpx.get(f"https://api.adzuna.com/v1/api/jobs/{COUNTRY}/search/{p + 1}",
                      headers={"User-Agent": UA}, params=params, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        total = data.get("count", total)
        res = data.get("results", [])
        fetched += 1
        if not res:
            break
        collected.extend(res)
        if total is not None and (p + 1) * page_size >= total:
            break
        if since and (res[-1].get("created") or "")[:10] < since:
            break                                   # reached jobs older than the window

    jobs = dedupe([_normalize(x) for x in collected])
    if remote_only:
        jobs = [j for j in jobs if j["mode"] == "remote"]
    return {
        "total": total if total is not None else len(jobs),
        "pages_fetched": fetched,
        "page_size": page_size,
        "direct_url": _direct_url(what, page_size),
        "jobs": jobs,
    }
