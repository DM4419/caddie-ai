"""API-tier board: Arbeitnow (https://www.arbeitnow.com/api/job-board-api).

Free Job Board API, no key. A paginated feed (100/page, ?page=N) sourced mostly
from ATS platforms and EU-weighted. There's no server-side search, so we page a
bounded budget and keep product-ish titles; the pipeline's role filter does the
authoritative cut. Dates arrive as a unix `created_at`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from adapters import dedupe
from engine.textutils import UA, detect_mode, detect_role, strip_html

API = "https://www.arbeitnow.com/api/job-board-api"
MAX_PAGES = 10
PRODUCT_HINT = ("product manager", "product owner", "head of product",
                "product lead", "lead product", "director of product",
                "vp product", "vp of product", "chief product", "founding product",
                "product management")


def _date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _is_product(title: str) -> bool:
    t = (title or "").lower()
    return any(h in t for h in PRODUCT_HINT)


def _normalize(j: dict) -> dict:
    title = j.get("title") or ""
    loc = j.get("location") or ""
    desc = strip_html(j.get("description") or "")
    mode = "remote" if j.get("remote") else detect_mode(f"{loc}\n{desc}")
    return {
        "role": detect_role(title),
        "title": title,
        "company": j.get("company_name") or "Unknown",
        "url": j.get("url") or "",
        "mode": mode,
        "location": loc,
        "salary": "",
        "description": f"{title}\n{loc}\n\n{desc}".strip(),
        "posted": _date(j.get("created_at")),
        "source": "Arbeitnow",
    }


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2, since: str = None) -> dict:
    hard_max = 8 if since else max(1, min(int(pages), MAX_PAGES))
    collected, fetched = [], 0
    for p in range(hard_max):
        r = httpx.get(API, headers={"User-Agent": UA},
                      params={"page": str(p + 1)}, timeout=30.0)
        r.raise_for_status()
        data = r.json().get("data") or []
        fetched += 1
        if not data:
            break
        collected.extend(data)

    jobs = dedupe([_normalize(x) for x in collected if _is_product(x.get("title"))])
    if remote_only:
        jobs = [j for j in jobs if j["mode"] == "remote"]
    if since:
        jobs = [j for j in jobs if not j["posted"] or j["posted"] >= since]
    return {
        "total": len(jobs),
        "pages_fetched": fetched,
        "page_size": page_size,
        "direct_url": API,
        "jobs": jobs,
    }
