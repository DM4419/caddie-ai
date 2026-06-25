"""API-tier board: TheirStack (https://api.theirstack.com/v1/jobs/search).

Paid aggregator (~$0.0015-$0.039 per job returned) covering 347k+ sources incl.
ATS platforms, with server-side dedup. Auth is a Bearer JWT in .env.

Billing is 1 credit per job RETURNED, so every query is a tight, focused funnel —
a job must match a target title AND a target-domain keyword AND the geo, within the
recency window. We never pay for off-target rows:
  * job_title_or          -> TITLES (Senior / Staff / Principal PM, Head of Product)
  * job_description_contains_or -> DOMAIN_SIGNALS (AI / SaaS / PropTech / B2B)
  * posted_at_max_age_days -> recency window

Geo is filtered server-side by `job_country_code_or` = BAND_COUNTRIES, a single lane
covering onsite/hybrid/remote within the collaboration timezone band (CET-1..CET+4 =
UTC 0..5). This is both cheaper (one lane, and we never pay for out-of-band rows) and
aligned with the pipeline's timezone_gate. remote_only narrows to remote in-band.

INCREMENTAL FETCH — to avoid re-paying for jobs we've already pulled, we persist a
watermark (the newest `discovered_at` we've fetched) plus a bounded ledger of job
ids in data/theirstack_state.json. Each fetch sends `discovered_at_gte` + a
`job_id_not` of seen ids, so a scan only bills NEW postings. The watermark is only
ADVANCED by record_fetched(), which the scan + import paths call AFTER fetching;
the preview path reads the watermark but never advances it, so previewing a board
can't make a later import skip those jobs.
"""
from __future__ import annotations

import json
import os
from datetime import date

import httpx
from dotenv import load_dotenv

from adapters import dedupe
from engine import store
from engine.textutils import UA, detect_role, fmt_salary, strip_html

_ROOT = os.path.dirname(os.path.dirname(__file__))
load_dotenv(os.path.join(_ROOT, ".env"))

API = "https://api.theirstack.com/v1/jobs/search"
STATE_PATH = os.path.join(_ROOT, "data", "theirstack_state.json")
MAX_PAGE_SIZE = 50
MAX_PAGES = 5                                        # hard cap so a scan can't run up the bill
DEFAULT_MAX_AGE = 30                                 # days, when no `since` is supplied
LEDGER_CAP = 2000                                    # bound the job_id_not list size

# Narrowed to the target shape: Senior / Staff / Principal PM + Head of Product.
# (Staff and Principal name the same IC tier; both kept for recall.)
TITLES = [
    "senior product manager",
    "staff product manager",
    "principal product manager",
    "head of product",
]

# A job must mention at least one of these — keeps the funnel on AI / SaaS /
# PropTech / B2B. Edit to retarget.
DOMAIN_SIGNALS = [
    # AI
    "ai", "artificial intelligence", "llm", "genai", "generative ai", "agentic",
    "machine learning", "voice ai",
    # SaaS / B2B
    "saas", "b2b saas", "b2b", "b2b2c", "enterprise software", "platform",
    # PropTech
    "proptech", "real estate",
]

# ISO-2 country codes in the timezone band (UTC 0..5): UK/Europe + Africa + Gulf +
# Caucasus through Pakistan. Keep in sync with profile.yaml timezone_gate.
BAND_COUNTRIES = [
    "GB", "IE", "PT", "ES", "FR", "DE", "IT", "NL", "BE", "LU", "CH", "AT", "PL",
    "CZ", "SK", "HU", "RO", "BG", "GR", "SE", "NO", "DK", "FI", "EE", "LV", "LT",
    "HR", "SI", "RS", "IS", "MT", "CY", "UA", "MD", "IL", "EG", "ZA", "KE", "NG",
    "MA", "TR", "SA", "AE", "QA", "KW", "BH", "OM", "GE", "AM", "AZ", "MU", "PK",
]


# ---- incremental-fetch state -------------------------------------------------
def _load_state() -> tuple[str, list]:
    """(watermark discovered_at, ledger of seen job ids). ('' , []) if absent."""
    try:
        with open(STATE_PATH) as f:
            d = json.load(f)
        return d.get("discovered_at_gte", "") or "", list(d.get("ids", []))
    except (FileNotFoundError, ValueError, OSError):
        return "", []


def _save_state(watermark: str, ids: list) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"discovered_at_gte": watermark, "ids": ids[-LEDGER_CAP:]}, f)
    os.replace(tmp, STATE_PATH)


def _iso_z(ts: str) -> str:
    """'2026-06-24T03:32:51.416000Z' -> '2026-06-24T03:32:51Z' (what the API wants)."""
    ts = (ts or "").strip()
    return (ts[:19] + "Z") if len(ts) >= 19 else ts


def record_fetched(raws: list) -> None:
    """Advance the watermark + ledger from a completed fetch. Called by the scan +
    import paths (NOT preview). No-op when no TheirStack rows are present."""
    ts = [r for r in raws if r.get("source") == "TheirStack"]
    if not ts:
        return
    watermark, ids = _load_state()
    discs = [r["ts_discovered_at"] for r in ts if r.get("ts_discovered_at")]
    if discs:
        watermark = max([watermark] + discs)
    new_ids = [r["ts_id"] for r in ts if r.get("ts_id") is not None]
    merged = list(dict.fromkeys(ids + new_ids))      # dedupe, keep order
    _save_state(watermark, merged)


# ---- fetch -------------------------------------------------------------------
def _mode(j: dict) -> str:
    if j.get("remote"):
        return "remote"
    if j.get("hybrid"):
        return "hybrid"
    return "onsite"


def _max_age_days(since: str | None) -> int:
    if not since:
        return DEFAULT_MAX_AGE
    try:
        y, m, d = (int(x) for x in since.split("-"))
        return max(1, (date.today() - date(y, m, d)).days + 1)
    except (ValueError, TypeError):
        return DEFAULT_MAX_AGE


def _normalize(j: dict) -> dict:
    title = j.get("job_title") or ""
    loc = j.get("location") or j.get("short_location") or j.get("country") or ""
    desc = strip_html(j.get("description") or "")
    return {
        "role": detect_role(title),
        "title": title,
        "company": j.get("company") or "Unknown",
        "url": j.get("final_url") or j.get("url") or j.get("source_url") or "",
        "mode": _mode(j),
        "location": loc,
        "salary": (j.get("salary_string")
                   or fmt_salary(j.get("min_annual_salary"), j.get("max_annual_salary"),
                                 j.get("salary_currency") or "")),
        "description": f"{title}\n{loc}\n\n{desc}".strip(),
        "posted": (j.get("date_posted") or "")[:10],
        "source": "TheirStack",
        "ts_id": j.get("id"),                           # carried for record_fetched (ignored by Job)
        "ts_discovered_at": _iso_z(j.get("discovered_at") or ""),
    }


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2, since: str = None) -> dict:
    key = os.environ.get("THEIRSTACK_API_KEY")
    if not key:
        raise RuntimeError("THEIRSTACK_API_KEY not set in .env")

    titles = [keyword.strip()] if keyword.strip() else TITLES
    limit = max(1, min(int(page_size), MAX_PAGE_SIZE))
    hard_max = max(1, min(int(pages), MAX_PAGES))
    watermark, ledger = _load_state()

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Accept": "application/json", "User-Agent": UA}
    base = {
        "include_total_results": False,
        "blur_company_data": False,
        "job_title_or": titles,
        "job_description_contains_or": DOMAIN_SIGNALS,
        "posted_at_max_age_days": _max_age_days(since),
        "order_by": [{"field": "date_posted", "desc": True}],
        "limit": limit,
    }
    if watermark:
        base["discovered_at_gte"] = watermark           # only bill jobs newer than last fetch

    # Single country-band lane (server-side geo). Covers onsite/hybrid/remote within
    # the band; remote_only narrows to remote. The ledger seeds job_id_not (held
    # constant across pages so it can't shift the page offsets).
    base["job_country_code_or"] = BAND_COUNTRIES
    if remote_only:
        base["remote"] = True
    exclude = list(ledger)

    collected, fetched = [], 0
    for p in range(hard_max):
        body = {**base, "page": p}
        if exclude:
            body["job_id_not"] = exclude
        r = httpx.post(API, headers=headers, json=body, timeout=45.0)
        r.raise_for_status()
        rows = r.json().get("data") or []
        fetched += 1
        if not rows:
            break
        collected.extend(rows)
        if len(rows) < limit:                           # last page
            break

    jobs = dedupe([_normalize(x) for x in collected])
    if remote_only:
        jobs = [j for j in jobs if j["mode"] == "remote"]
    return {
        "total": len(jobs),
        "pages_fetched": fetched,
        "page_size": limit,
        "direct_url": "https://app.theirstack.com/jobs",
        "jobs": jobs,
    }
