"""Board adapters by tier: api (this phase), browser-assisted, manual (later)."""
from urllib.parse import urlparse


def url_key(url: str) -> str:
    """Stable identity for a job URL: host + path, ignoring tracking query params.

    The same posting arrives with different `?se=…&v=…` / `utm_*` query strings
    across fetches, but the path (e.g. Adzuna `/jobs/land/ad/<id>`, LinkedIn
    `/jobs/view/<id>`) is the stable job id. So distinct roles at the same
    recruiter stay distinct, while re-fetches of one job collapse.
    """
    if not url:
        return ""
    try:
        p = urlparse(url)
        return f"{p.netloc.lower()}{p.path.rstrip('/')}"
    except ValueError:
        return url


def dedupe(jobs: list) -> list:
    """Drop repeat jobs, keyed by URL path (falling back to company|title)."""
    seen, out = set(), []
    for j in jobs:
        key = url_key(j.get("url")) or (
            (j.get("company", "") or "").strip().lower() + "|"
            + (j.get("title") or j.get("role") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out
