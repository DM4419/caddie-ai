"""Generic listing importer: pull jobs out of any page's schema.org JobPosting
structured data (JSON-LD). Many job boards embed this for SEO even when the
visible cards are JS-rendered, so this works on a lot of "filtered search" URLs
without per-board code.

`extract_jobpostings(html, source)` is the shared parser — the browser tier
(adapters/browser.py) runs it on the Playwright-rendered HTML too. `fetch()`
is the httpx entry point used for static pages.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from engine.textutils import UA, detect_mode, detect_role, normalize_mode, strip_html

TIMEOUT = 25.0
# Safety ceiling on pages per search; the actual depth is the per-board `pages`
# setting (default 5). The age-horizon early-stop trims this further on
# date-sorted boards; non-sorted boards (e.g. Reed) page up to the budget.
MAX_PAGES = 25


def _addr(loc) -> str:
    """Flatten a schema.org location (PostalAddress / Place / list) to a string."""
    if isinstance(loc, list):
        return ", ".join(filter(None, (_addr(x) for x in loc)))
    if isinstance(loc, str):
        return loc.strip()
    if not isinstance(loc, dict):
        return ""
    if loc.get("address"):
        return _addr(loc["address"])
    parts = [loc.get("addressLocality"), loc.get("addressRegion"),
             loc.get("addressCountry")]
    parts = [p.get("name") if isinstance(p, dict) else p for p in parts]
    return ", ".join(filter(None, (str(p).strip() for p in parts if p)))


def _node_to_job(node: dict, base_url: str, source: str) -> dict | None:
    title = (node.get("title") or "").strip()
    if not title:
        return None
    org = node.get("hiringOrganization")
    company = (org.get("name") if isinstance(org, dict) else org) or source or "Unknown"
    loc = _addr(node.get("jobLocation"))
    if not loc and node.get("applicantLocationRequirements"):
        loc = _addr(node["applicantLocationRequirements"])
    remote = str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE"
    body = strip_html(node.get("description", ""))
    url = node.get("url") or node.get("sameAs") or ""
    if url and base_url:
        url = urljoin(base_url, url)
    posted = str(node.get("datePosted", ""))[:10]
    mode = "remote" if remote else detect_mode(f"{loc}\n{body}")
    return {
        "role": detect_role(title), "title": title, "company": str(company).strip(),
        "url": url, "location": loc, "mode": mode,
        "description": f"{title}\n{loc}\n\n{body}".strip(),
        "posted": posted, "source": source,
    }


def _walk(node, out: list):
    """Collect every JobPosting dict reachable in a JSON-LD structure."""
    if isinstance(node, list):
        for n in node:
            _walk(n, out)
        return
    if not isinstance(node, dict):
        return
    types = node.get("@type", "")
    types = types if isinstance(types, list) else [types]
    if "JobPosting" in types:
        out.append(node)
    if "@graph" in node:
        _walk(node["@graph"], out)
    if "itemListElement" in node:            # ItemList of postings
        for el in node["itemListElement"]:
            _walk(el.get("item", el) if isinstance(el, dict) else el, out)


def extract_jobpostings(html: str, source: str = "", base_url: str = "") -> list:
    """Parse all JobPosting JSON-LD nodes from an HTML string -> raw job dicts."""
    soup = BeautifulSoup(html or "", "html.parser")
    nodes: list = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        data = None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # JobPosting JSON-LD frequently has raw newlines/tabs inside
            # "description" (invalid JSON) — strip control chars and retry
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]+", " ", raw))
            except (json.JSONDecodeError, TypeError):
                data = None
        if data is not None:
            _walk(data, nodes)
    jobs, seen = [], set()
    for n in nodes:
        job = _node_to_job(n, base_url, source)
        if not job:
            continue
        key = (job["url"] or "").lower() or (job["company"] + "|" + job["title"]).lower()
        if key in seen:
            continue
        seen.add(key)
        jobs.append(job)
    return jobs


def _source_name(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    return host.split(".")[0].title() if host else "Listing"


def _next_url(html: str, cur: str) -> str:
    """The board's 'next page' link, if any (rel=next / aria-label / 'Next')."""
    soup = BeautifulSoup(html, "html.parser")
    a = (soup.find("a", rel="next")
         or soup.find("a", attrs={"aria-label": re.compile(r"next", re.I)})
         or soup.find("a", string=re.compile(r"^\s*next", re.I)))
    return urljoin(cur, a["href"]) if a and a.get("href") else ""


def _expired_urls(html: str, base: str) -> set:
    """URLs of result cards explicitly marked filled/expired (e.g. intelligentpeople's
    `div.expired--message`), so they're dropped whichever extractor produced them."""
    from adapters import url_key
    soup = BeautifulSoup(html or "", "html.parser")
    out = set()
    for mark in soup.find_all(class_=re.compile(r"expired", re.I)):
        a = mark.find_parent("a", href=True)
        if not a:
            card = mark.find_parent(["article", "li", "div"])
            a = card.find("a", href=True) if card else None
        if a and a.get("href"):
            out.add(url_key(urljoin(base, a["href"])))
    return out


def _extract_page(html: str, src: str, base: str) -> list:
    jobs = extract_jobpostings(html, source=src, base_url=base)
    if not jobs:
        from adapters import browser
        jobs = browser.heuristic_jobs(html, base, src)
    expired = _expired_urls(html, base)          # drop filled/expired postings at fetch time
    if expired:
        from adapters import url_key
        jobs = [j for j in jobs if url_key(j.get("url", "")) not in expired]
    return jobs


def fetch(url: str, keyword: str = "", remote_only: bool = False,
          page_size: int = 50, pages: int = 1, since: str = None,
          filter_kw: bool = True) -> dict:
    """Fetch a listing URL over HTTP, following pagination. Prefers JobPosting
    JSON-LD, else DOM-card heuristics (Reed etc. are server-rendered static HTML,
    no browser needed). Follows the board's 'next' link up to `pages`, stopping
    early once a page adds no jobs within the age horizon. `filter_kw=False` when
    the URL itself is already the search."""
    import time as _time
    from adapters import url_key
    collected, seen, cur = [], set(), url
    src = _source_name(url)
    pages_fetched = 0
    for p in range(max(1, min(int(pages), MAX_PAGES))):
        r = httpx.get(cur, headers={"User-Agent": UA}, follow_redirects=True, timeout=TIMEOUT)
        if r.status_code != 200:
            break
        pages_fetched += 1
        base = str(r.url)
        fresh_in_window = 0
        for j in _extract_page(r.text, src, base):
            key = url_key(j["url"]) or (j["company"] + "|" + j["title"]).lower()
            if key in seen:
                continue
            seen.add(key)
            collected.append(j)
            if not since or not j["posted"] or j["posted"] >= since:
                fresh_in_window += 1
        nxt = _next_url(r.text, base)
        if not nxt or (since and fresh_in_window == 0):   # nothing new in window -> stop
            break
        cur = nxt
        _time.sleep(0.4)                                  # be polite between pages
    jobs = collected
    kw = keyword.strip().lower()
    if kw and filter_kw:
        jobs = [j for j in jobs if kw in (j["title"] + " " + j["description"]).lower()]
    if remote_only:
        jobs = [j for j in jobs if j["mode"] == "remote"]
    return {"jobs": jobs, "total": len(jobs), "pages_fetched": pages_fetched,
            "page_size": page_size, "direct_url": url}


def probe(url: str) -> int:
    """How many jobs a static fetch can see (0 => needs the browser tier)."""
    try:
        return fetch(url)["total"]
    except Exception:
        return 0


class _ListingFetcher:
    """Binds a listing URL (+ optional {q} search template) to the board .fetch()
    interface. With a template it's QUERY_BASED so board_fetch fans out over the
    role titles — all via fast httpx, no browser."""

    def __init__(self, url: str, search_template: str = "", sep: str = "%20"):
        from urllib.parse import quote
        self._quote = quote
        self.url = url
        self.template = search_template or ""
        self.sep = sep or "%20"
        self.QUERY_BASED = bool(self.template)

    def _url_for(self, keyword: str) -> str:
        kw = keyword.strip()
        if self.template and "{q}" in self.template and kw:
            value = self.sep.join(self._quote(w) for w in kw.split())
            return self.template.replace("{q}", value)
        return self.url

    def fetch(self, keyword: str = "", remote_only: bool = False,
              page_size: int = 50, pages: int = 1, since: str = None) -> dict:
        # when a template builds the URL, the board already filtered — don't
        # re-filter by the literal keyword (the role gate handles relevance)
        return fetch(self._url_for(keyword), keyword=keyword, remote_only=remote_only,
                     page_size=page_size, pages=pages, since=since,
                     filter_kw=not self.template)


def board_fetcher(url: str, search_template: str = "", sep: str = "%20") -> _ListingFetcher:
    return _ListingFetcher(url, search_template, sep)
