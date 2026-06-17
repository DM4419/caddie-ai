"""Check whether a job posting is still live at its source URL.

Boards keep serving listings long after the role is pulled from the company's own
site, so a recently-fetched job can already be dead. We do ONE GET (following
redirects) and classify the result. Conservative: when unsure, we say "live" —
better a false-live than wrongly hiding a real role.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from .textutils import UA

# Phrases that mean "this posting is gone", matched case-insensitively in the body.
DEAD_PHRASES = (
    "no longer available", "no longer accepting", "position has been filled",
    "this job has expired", "job is no longer", "posting has expired",
    "this position is closed", "job not found", "page not found",
    "role has been filled", "vacancy is closed", "applications are closed",
    "this listing has expired", "opportunity is no longer",
    "vacancy has been filled", "has been filled", "this role is closed",
    "this vacancy is no longer", "position is no longer", "this position has been filled",
    "upload your cv to be notified", "notified of similar positions",
    "applications have closed", "no longer open",
)


def looks_dead(text: str) -> str:
    """Return the matched dead phrase if the body text says the posting is gone, else ''."""
    low = (text or "").lower()
    for phrase in DEAD_PHRASES:
        if phrase in low:
            return phrase
    return ""


# Hosts that reject automated checks (bot protection) — not worth probing.
UNVERIFIABLE_HOSTS = {"adzuna.co.uk", "adzuna.com"}


def _path_depth(url: str) -> int:
    try:
        return len([s for s in urlparse(url).path.split("/") if s])
    except ValueError:
        return 0


def check_url(url: str) -> dict:
    """Return {"live": live|expired|error, "note": <one line>} for a job URL."""
    if not (url or "").strip():
        return {"live": "error", "note": "no URL to check"}
    # Some aggregator/apply domains block non-browser clients (always 429/403),
    # so a liveness check is meaningless — report "unknown" without wasting a call.
    host = urlparse(url).netloc.lower().replace("www.", "")
    if any(host == h or host.endswith("." + h) for h in UNVERIFIABLE_HOSTS):
        return {"live": "", "note": f"{host} can't be liveness-checked"}
    resp = None
    for attempt in range(2):
        try:
            resp = httpx.get(url, headers={"User-Agent": UA},
                             follow_redirects=True, timeout=15.0)
        except Exception as e:
            return {"live": "error", "note": f"could not reach: {type(e).__name__}"}
        if resp.status_code != 429:
            break
        # rate-limited — wait the server-requested time (capped) and retry once
        try:
            wait = min(float(resp.headers.get("Retry-After", 2)), 10.0)
        except ValueError:
            wait = 2.0
        if attempt == 0:
            time.sleep(wait)
    if resp is not None and resp.status_code == 429:
        return {"live": "error", "note": "rate-limited by source (429)"}

    if resp.status_code in (404, 410):
        return {"live": "expired", "note": f"source returned {resp.status_code}"}
    if resp.status_code >= 400:
        return {"live": "error", "note": f"source returned {resp.status_code}"}

    # Removed postings on many boards redirect to the careers/listing root.
    if _path_depth(url) >= 1 and _path_depth(str(resp.url)) == 0:
        return {"live": "expired", "note": "redirected to site root (posting removed)"}

    body = resp.text.lower()
    for phrase in DEAD_PHRASES:
        if phrase in body:
            return {"live": "expired", "note": f"page says “{phrase}”"}
    return {"live": "live", "note": "reachable"}
