"""Turn a job URL (or pasted text) into a normalized Job draft.

For known ATS boards (Greenhouse/Lever/Ashby/Workable) we use their JSON API via
`adapters.api` — reliable, full description, correct company. For everything else
we fall back to generic HTML extraction, which is good enough for many static
pages but can't see JS-rendered content.
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .textutils import UA, detect_mode, detect_role, strip_html  # noqa: F401 (re-exported)


def _company_from_url(url: str) -> str:
    """Best-effort company name from common ATS URL shapes."""
    try:
        p = urlparse(url)
    except ValueError:
        return ""
    host = p.netloc.lower().replace("www.", "")
    parts = [seg for seg in p.path.split("/") if seg]
    if ("lever.co" in host or "greenhouse.io" in host or "ashbyhq.com" in host) and parts:
        return parts[0].replace("-", " ").title()
    base = host.split(":")[0].split(".")
    return (base[-2] if len(base) >= 2 else host).title()


def _clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def from_text(text: str, url: str = "") -> dict:
    """Build a Job dict from a pasted description (no network)."""
    company = _company_from_url(url) if url else "Pasted JD"
    first_line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return {
        "role": detect_role(text),
        "title": first_line[:120],
        "company": company,
        "url": url,
        "mode": detect_mode(text),
        "description": text.strip(),
    }


def _fetch_generic(url: str) -> dict:
    """Generic HTML extraction — fallback for non-ATS / static pages."""
    resp = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20.0)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("meta", property="og:title")
    title = title_tag["content"].strip() if title_tag and title_tag.get("content") else ""
    if not title and soup.title:
        title = soup.title.get_text().strip()
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text().strip() if h1 else ""

    site = soup.find("meta", property="og:site_name")
    company = site["content"].strip() if site and site.get("content") else _company_from_url(url)

    description = _clean_text(soup)
    return {
        "role": detect_role(title or description),
        "title": title,
        "company": company or "Unknown",
        "url": url,
        "mode": detect_mode(description),
        "description": description,
    }


def fetch(url: str) -> dict:
    """Known ATS board -> JSON API adapter; otherwise generic HTML."""
    from adapters import api as api_adapter
    data = api_adapter.fetch(url)
    if data and data.get("description"):
        return data
    return _fetch_generic(url)


def fetch_or_paste(url: str = "", text: str = "") -> dict:
    """URL given -> fetch (fall back to pasted text on failure). Else use text."""
    url = (url or "").strip()
    text = (text or "").strip()
    if url:
        try:
            job = fetch(url)
            if not job["description"] and text:
                job["description"] = text
            return job
        except Exception:
            if text:
                return from_text(text, url)
            raise
    return from_text(text, url)
