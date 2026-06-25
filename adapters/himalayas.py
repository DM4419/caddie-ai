"""API-tier board: Himalayas (https://himalayas.app/jobs/api).

Free JSON API, no auth, built to feed automation. Two real constraints: a 20-job
cap per request (offset-paginated), and NO server-side filtering — the endpoint
returns a global, non-date-ordered feed of ~89k roles. So we page a bounded
budget, keep product-ish roles client-side, and rely on the pipeline's role +
recency filters for the final cut. Yield per scan is therefore limited by design.

Himalayas asks for attribution back to source: we keep the himalayas.app URL and
set source="Himalayas". Everything here is remote.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from adapters import dedupe
from engine.textutils import UA, detect_role, strip_html

API = "https://himalayas.app/jobs/api"
REQ_CAP = 20                                        # Himalayas caps each request at 20
MAX_PAGES = 30                                       # bounded crawl (~600 newest-ish roles)
PRODUCT_HINT = ("product manager", "product owner", "head of product",
                "product lead", "lead product", "director of product",
                "vp product", "vp of product", "chief product", "founding product",
                "product management")


def _date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _is_product(j: dict) -> bool:
    t = (j.get("title") or "").lower()
    if any(h in t for h in PRODUCT_HINT):
        return True
    cats = " ".join(j.get("categories") or []).lower()
    return "product-management" in cats or "product-manager" in cats


def _salary(j: dict) -> str:
    mn, mx, cur = j.get("minSalary"), j.get("maxSalary"), (j.get("currency") or "")
    if not mn and not mx:
        return ""
    sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(cur, cur + " " if cur else "")
    parts = [f"{sym}{int(v) // 1000}k" for v in (mn, mx) if v]
    return " - ".join(parts) + (f"/{j.get('salaryPeriod')}" if j.get("salaryPeriod") else "")


def _normalize(j: dict) -> dict:
    title = j.get("title") or ""
    loc = ", ".join(j.get("locationRestrictions") or []) or "Remote"
    desc = strip_html(j.get("description") or j.get("excerpt") or "")
    return {
        "role": detect_role(title),
        "title": title,
        "company": j.get("companyName") or "Unknown",
        "url": j.get("applicationLink") or j.get("guid") or "",
        "mode": "remote",                           # Himalayas is remote-only
        "location": loc,
        "salary": _salary(j),
        "description": f"{title}\n{loc}\n\n{desc}".strip(),
        "posted": _date(j.get("pubDate")),
        "source": "Himalayas",
    }


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2, since: str = None) -> dict:
    hard_max = MAX_PAGES if since else max(1, min(int(pages), MAX_PAGES))
    collected, fetched = [], 0
    for p in range(hard_max):
        r = httpx.get(API, headers={"User-Agent": UA},
                      params={"limit": REQ_CAP, "offset": p * REQ_CAP}, timeout=30.0)
        r.raise_for_status()
        rows = r.json().get("jobs") or []
        fetched += 1
        if not rows:
            break
        collected.extend(rows)

    jobs = dedupe([_normalize(x) for x in collected if _is_product(x)])
    if since:
        jobs = [j for j in jobs if not j["posted"] or j["posted"] >= since]
    return {
        "total": len(jobs),
        "pages_fetched": fetched,
        "page_size": REQ_CAP,
        "direct_url": API,
        "jobs": jobs,
    }
