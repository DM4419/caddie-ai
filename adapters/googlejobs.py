"""API-tier board: Google Jobs via SerpAPI (https://serpapi.com, engine=google_jobs).

Aggregates Google's job graph (LinkedIn, employer sites, niche boards). Paid per
search (free tier ~100/month), so it's QUERY_BASED — board_fetch fans out over the
profile's role queries, one search per title, paginated by next_page_token. Biased
to the UK locale (gl=uk); the pipeline's timezone gate does the final geo cut.

Each result carries the full JD `description` and an `apply_options` link, so drafts
have real text to work from. Key in .env (SERPAPI_KEY).
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

from adapters import dedupe
from engine.textutils import UA, detect_mode, detect_role, strip_html

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

API = "https://serpapi.com/search"
MAX_PAGES = 3                                        # cap searches/scan (free quota is ~100/mo)
QUERY_BASED = True                                   # general engine: fan out over role_queries
_AGO_RE = re.compile(r"(\d+)\s*(day|week|month|year)", re.I)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _posted(text: str) -> str:
    """'1 day ago' / 'Just posted' -> YYYY-MM-DD (best-effort, '' if unparseable)."""
    t = (text or "").lower()
    if not t:
        return ""
    if "today" in t or "hour" in t or "minute" in t or "just" in t:
        return date.today().isoformat()
    m = _AGO_RE.search(t)
    if not m:
        return ""
    days = _UNIT_DAYS[m.group(2)] * int(m.group(1))
    return (date.today() - timedelta(days=days)).isoformat()


def _normalize(j: dict) -> dict:
    title = j.get("title") or j.get("job_title") or ""
    loc = j.get("location") or ""
    det = j.get("detected_extensions") or {}
    desc = strip_html(j.get("description") or "")
    apply_link = (j.get("apply_options") or [{}])[0].get("link", "")
    url = apply_link or j.get("source_link") or j.get("share_link") or ""
    mode = "remote" if det.get("work_from_home") else detect_mode(f"{title}\n{loc}\n{desc[:400]}")
    return {
        "role": detect_role(title),
        "title": title,
        "company": j.get("company_name") or "Unknown",
        "url": url,
        "mode": mode,
        "location": loc,
        "salary": det.get("salary") or "",
        "description": f"{title}\n{loc}\n\n{desc}".strip(),
        "posted": _posted(det.get("posted_at") or ""),
        "source": "Google Jobs",
    }


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 1, since: str = None) -> dict:
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        raise RuntimeError("SERPAPI_KEY not set in .env")
    q = keyword.strip() or "product manager"
    hard_max = max(1, min(int(pages), MAX_PAGES))

    params = {"engine": "google_jobs", "q": q, "location": "United Kingdom",
              "hl": "en", "gl": "uk", "api_key": key}
    if remote_only:
        params["ltype"] = "1"                        # Google's work-from-home filter

    collected, fetched, token = [], 0, None
    for _ in range(hard_max):
        if token:
            params["next_page_token"] = token
        r = httpx.get(API, params=params, headers={"User-Agent": UA}, timeout=40.0)
        r.raise_for_status()
        data = r.json()
        err = (data.get("error") or "")
        if err:
            # "hasn't returned any results" is a normal empty page, not a failure;
            # only a real error (bad key / quota) should surface.
            if "any results" in err.lower():
                break
            raise RuntimeError(f"SerpAPI: {err}")
        rows = data.get("jobs_results") or []
        fetched += 1
        if not rows:
            break
        collected.extend(rows)
        token = (data.get("serpapi_pagination") or {}).get("next_page_token")
        if not token:
            break

    jobs = dedupe([_normalize(x) for x in collected])
    if remote_only:
        jobs = [j for j in jobs if j["mode"] == "remote"]
    if since:
        jobs = [j for j in jobs if not j["posted"] or j["posted"] >= since]
    return {
        "total": len(jobs),
        "pages_fetched": fetched,
        "page_size": page_size,
        "direct_url": f"https://www.google.com/search?q={q.replace(' ', '+')}&ibp=htl;jobs",
        "jobs": jobs,
    }
