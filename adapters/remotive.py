"""API-tier board: Remotive (https://remotive.com/api/remote-jobs).

Public JSON API, no auth. The `search` param is ignored server-side (it returns a
fixed recent feed), but each posting carries a clean `category`, so we keep the
Product-Management ones client-side. Everything here is remote by definition.

Remotive's ToS asks integrators to keep the source link and credit Remotive: we
store the remotive.com URL and set source="Remotive". Jobs are delayed ~24h.
"""
from __future__ import annotations

import httpx

from adapters import dedupe
from engine.textutils import UA, detect_role, strip_html

API = "https://remotive.com/api/remote-jobs"
MAX_PAGE_SIZE = 50
MAX_PAGES = 10
PRODUCT_HINT = ("product manager", "product owner", "head of product",
                "product lead", "lead product", "director of product",
                "vp product", "vp of product", "chief product", "founding product")


def _is_product(j: dict) -> bool:
    if (j.get("category") or "").strip().lower() == "product management":
        return True
    t = (j.get("title") or "").lower()
    return any(h in t for h in PRODUCT_HINT)


def _normalize(j: dict) -> dict:
    title = j.get("title") or ""
    loc = j.get("candidate_required_location") or "Remote"
    tags = ", ".join(j.get("tags") or [])
    desc = strip_html(j.get("description") or "")
    return {
        "role": detect_role(title),
        "title": title,
        "company": j.get("company_name") or "Unknown",
        "url": j.get("url") or "",
        "mode": "remote",                          # Remotive is remote by definition
        "location": loc,
        "salary": (j.get("salary") or "").strip(),  # Remotive pre-formats this
        "description": f"{title}\n{loc}\nTags: {tags}\n\n{desc}".strip(),
        "posted": (j.get("publication_date") or "")[:10],
        "source": "Remotive",
    }


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2, since: str = None) -> dict:
    r = httpx.get(API, headers={"User-Agent": UA}, timeout=30.0)
    r.raise_for_status()
    items = [x for x in (r.json().get("jobs") or [])
             if isinstance(x, dict) and x.get("title") and _is_product(x)]

    jobs = dedupe([_normalize(x) for x in items])
    if since:                                       # keep the whole window (unknown-date kept)
        jobs = [j for j in jobs if not j["posted"] or j["posted"] >= since]
        total = len(jobs)
    else:
        total = len(jobs)
        cap = max(1, min(int(page_size), MAX_PAGE_SIZE)) * max(1, min(int(pages), MAX_PAGES))
        jobs = jobs[:cap]
    return {
        "total": total,
        "pages_fetched": 1,                         # single feed, no pagination
        "page_size": page_size,
        "direct_url": API,
        "jobs": jobs,
    }
