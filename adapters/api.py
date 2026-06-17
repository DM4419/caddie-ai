"""API-tier adapters: turn a known ATS job URL into a normalized Job dict via the
board's public JSON API, instead of scraping the JS-rendered page.

Supported: Greenhouse, Lever, Ashby, Workable. Each `_<provider>(url)` returns
{role, company, url, mode, description} or raises. `fetch(url)` is the entry
point — returns the dict, or None when the URL isn't a known board or the call
fails (caller then falls back to generic HTML fetch).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from engine.textutils import UA, detect_mode, detect_role, normalize_mode, strip_html

TIMEOUT = 20.0


def _get(url: str, **kw) -> httpx.Response:
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                  follow_redirects=True, **kw)
    r.raise_for_status()
    return r


def detect(url: str) -> Optional[str]:
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "workable.com" in host:
        return "workable"
    return None


def fetch(url: str) -> Optional[dict]:
    provider = detect(url)
    if not provider:
        return None
    try:
        return globals()[f"_{provider}"](url)
    except Exception:
        return None  # let the caller fall back to generic HTML


def _slug_company(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


# ---- Greenhouse ----------------------------------------------------------
def _greenhouse(url: str) -> dict:
    p = urlparse(url)
    parts = [s for s in p.path.split("/") if s]
    co = jid = None
    if "embed" in parts:                       # /embed/job_app?for=<co>&token=<id>
        qs = parse_qs(p.query)
        co = (qs.get("for") or [None])[0]
        jid = (qs.get("token") or [None])[0]
    else:
        if "jobs" in parts:
            i = parts.index("jobs")
            co = parts[i - 1] if i >= 1 else None
            jid = parts[i + 1] if i + 1 < len(parts) else None
        if not co:                              # <co>.greenhouse.io form
            sub = p.netloc.lower().split(".")[0]
            if sub not in ("boards", "job-boards", "www", "boards-api"):
                co = sub
        if not jid:
            jid = next((s for s in reversed(parts) if s.isdigit()), None)
    if not co or not jid:
        raise ValueError("could not parse greenhouse company/job id")

    d = _get(f"https://boards-api.greenhouse.io/v1/boards/{co}/jobs/{jid}").json()
    title = d.get("title", "")
    loc = (d.get("location") or {}).get("name", "")
    body = strip_html(d.get("content", ""))
    return {
        "role": detect_role(title),
        "title": title,
        "company": d.get("company_name") or _slug_company(co),
        "url": d.get("absolute_url") or url,
        "location": loc,
        "mode": detect_mode(f"{loc}\n{body}"),
        "description": f"{title}\n{loc}\n\n{body}".strip(),
    }


# ---- Lever ---------------------------------------------------------------
def _lever(url: str) -> dict:
    parts = [s for s in urlparse(url).path.split("/") if s]
    if len(parts) < 2:
        raise ValueError("could not parse lever company/job id")
    co, jid = parts[0], parts[1]
    d = _get(f"https://api.lever.co/v0/postings/{co}/{jid}?mode=json").json()
    title = d.get("text", "")
    cats = d.get("categories") or {}
    meta = " · ".join(filter(None, [cats.get("location"), cats.get("department"),
                                     cats.get("team"), cats.get("commitment")]))
    body = d.get("descriptionPlain") or strip_html(d.get("description", ""))
    lists = "\n".join(
        f"{l.get('text','')}\n{strip_html(l.get('content',''))}" for l in d.get("lists", []))
    extra = d.get("additionalPlain") or strip_html(d.get("additional", ""))
    description = "\n\n".join(filter(None, [f"{title}\n{meta}", body, lists, extra]))
    return {
        "role": detect_role(title),
        "title": title,
        "company": _slug_company(co),
        "url": d.get("hostedUrl") or url,
        "location": cats.get("location") or "",
        "mode": normalize_mode(d.get("workplaceType", ""), description),
        "description": description.strip(),
    }


# ---- Ashby ---------------------------------------------------------------
def _ashby(url: str) -> dict:
    parts = [s for s in urlparse(url).path.split("/") if s]
    if len(parts) < 2:
        raise ValueError("could not parse ashby company/job id")
    co, jid = parts[0], parts[1]
    jobs = _get(
        f"https://api.ashbyhq.com/posting-api/job-board/{co}?includeCompensation=true"
    ).json().get("jobs", [])
    job = next((j for j in jobs if j.get("id") == jid
                or (j.get("jobUrl") or "").rstrip("/").endswith(jid)), None)
    if not job:
        raise ValueError("ashby job id not found on board")
    title = job.get("title", "")
    loc = job.get("location", "") or ""
    body = job.get("descriptionPlain") or strip_html(job.get("descriptionHtml", ""))
    explicit = job.get("workplaceType") or ("remote" if job.get("isRemote") else "")
    return {
        "role": detect_role(title),
        "title": title,
        "company": _slug_company(co),
        "url": job.get("jobUrl") or url,
        "location": loc,
        "mode": normalize_mode(explicit, f"{loc}\n{body}"),
        "description": f"{title}\n{loc}\n\n{body}".strip(),
    }


# ---- Workable ------------------------------------------------------------
def _workable(url: str) -> dict:
    parts = [s for s in urlparse(url).path.split("/") if s]
    acct = parts[0] if parts else ""
    shortcode = parts[parts.index("j") + 1] if "j" in parts else (parts[-1] if parts else "")
    if not acct or not shortcode:
        raise ValueError("could not parse workable account/shortcode")
    d = _get(f"https://apply.workable.com/api/v2/accounts/{acct}/jobs/{shortcode}").json()
    title = d.get("title", "")
    loc = d.get("location") or {}
    locstr = ", ".join(filter(None, [loc.get("city"), loc.get("country")])) if isinstance(loc, dict) else ""
    body = strip_html(" ".join(filter(None, [
        d.get("description", ""), d.get("requirements", ""), d.get("benefits", "")])))
    explicit = d.get("workplace") or ("remote" if d.get("remote") else "")

    company = _slug_company(acct)
    try:
        acc = _get(f"https://www.workable.com/api/accounts/{acct}?details=true").json()
        if acc.get("name"):
            company = acc["name"]
    except Exception:
        pass
    return {
        "role": detect_role(title),
        "title": title,
        "company": company,
        "url": d.get("shortlink") or url,
        "location": locstr,
        "mode": normalize_mode(explicit, f"{locstr}\n{body}"),
        "description": f"{title}\n{locstr}\n\n{body}".strip(),
    }
