"""Listing-tier adapter for Getro-powered VC portfolio job boards.

EU VC talent networks (Point Nine, Seedcamp, Cherry, Speedinvest, Atomico…) all run
on Getro. They don't expose JobPosting JSON-LD, but they server-render a Next.js
`__NEXT_DATA__` blob on a plain GET, with the jobs at
`props.pageProps.initialState.jobs.found[]` — a clean array of ~20 structured jobs.
Getro also honours a server-side `?q=` search, so we target product roles directly.

One adapter, many boards: board_fetcher(url, source) binds a board to the standard
.fetch() interface (tier: getro in boards.yaml). Each job's `url` is the real
underlying ATS posting, so drafting can pull the full JD from there later.

v1 reads page 1 only (~20 jobs per query); deeper pages need Getro's POST search API.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

from adapters import dedupe
from engine.textutils import UA, detect_role, normalize_mode

SEARCH_Q = "product manager"                          # server-side filter to product roles


def _date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _jobs_blob(html: str) -> list:
    soup = BeautifulSoup(html or "", "html.parser")
    nd = soup.find("script", id="__NEXT_DATA__")
    if not nd or not nd.string:
        return []
    try:
        d = json.loads(nd.string)
        return d["props"]["pageProps"]["initialState"]["jobs"]["found"] or []
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _normalize(j: dict, source: str) -> dict:
    org = j.get("organization") or {}
    company = org.get("name") or "Unknown"
    locs = j.get("locations") or j.get("searchableLocations") or []
    location = ", ".join(str(x) for x in locs if x)
    title = j.get("title") or ""
    skills = ", ".join(j.get("skills") or [])
    return {
        "role": detect_role(title),
        "title": title,
        "company": company,
        "url": j.get("url") or "",                     # real underlying ATS posting
        "mode": normalize_mode((j.get("workMode") or "").replace("_", " "), location),
        "location": location,
        "salary": "",
        "description": f"{title}\n{company}\n{location}\nSkills: {skills}".strip(),
        "posted": _date(j.get("createdAt")),
        "source": source or "Getro",
    }


def fetch(url: str, source: str = "", keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 1, since: str = None) -> dict:
    q = keyword.strip() or SEARCH_Q
    full = f"{url}?{urlencode({'q': q})}"
    r = httpx.get(full, headers={"User-Agent": UA}, follow_redirects=True, timeout=30.0)
    r.raise_for_status()
    jobs = dedupe([_normalize(j, source) for j in _jobs_blob(r.text)])
    if remote_only:
        jobs = [j for j in jobs if j["mode"] == "remote"]
    if since:
        jobs = [j for j in jobs if not j["posted"] or j["posted"] >= since]
    return {"total": len(jobs), "pages_fetched": 1, "page_size": page_size,
            "direct_url": full, "jobs": jobs}


class _GetroFetcher:
    """Binds a Getro board URL + display source to the board .fetch() interface."""

    def __init__(self, url: str, source: str = ""):
        self.url = url
        self.source = source or urlparse(url).netloc

    def fetch(self, keyword: str = "", remote_only: bool = False,
              page_size: int = 20, pages: int = 1, since: str = None) -> dict:
        return fetch(self.url, self.source, keyword=keyword, remote_only=remote_only,
                     page_size=page_size, pages=pages, since=since)


def board_fetcher(url: str, source: str = "") -> _GetroFetcher:
    return _GetroFetcher(url, source)
