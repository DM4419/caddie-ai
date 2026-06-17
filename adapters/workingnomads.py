"""Board: Working Nomads.

Their public `exposed_jobs` JSON feed only exposes ~46 recent jobs across all
categories, so it misses most Product roles. The full board lives behind an
AngularJS SPA at /jobs?tag=<tag>; the job titles ARE visible to logged-out
users, so we render that tag page with the browser tier and scrape it.

MANUAL_ONLY: rendering is slow + rate-limited, so this is scanned on demand from
Settings → Boards, not by the fast auto-Refresh.
"""
from __future__ import annotations

from urllib.parse import quote

MANUAL_ONLY = True
BASE = "https://www.workingnomads.com"


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 50, pages: int = 1, since: str = None) -> dict:
    from adapters import browser, dedupe
    tag = (keyword or "product").strip().lower()
    url = f"{BASE}/jobs?tag={quote(tag)}"
    html = browser.render(url)
    jobs = dedupe(browser.heuristic_jobs(html, url, "Working Nomads"))
    for j in jobs:
        j["source"] = "Working Nomads"
        if not (j.get("location") or "").strip():
            j["location"] = "Remote"
            j["mode"] = "remote"
    if remote_only:
        jobs = [j for j in jobs if j["mode"] == "remote"]
    if since:
        jobs = [j for j in jobs if not j["posted"] or j["posted"] >= since]
    return {"jobs": jobs, "total": len(jobs), "pages_fetched": 1,
            "page_size": page_size, "direct_url": url}
