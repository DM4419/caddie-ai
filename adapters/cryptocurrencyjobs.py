"""Browser/HTML-tier board: Cryptocurrency Jobs (cryptocurrencyjobs.co).

No public API token, so we scrape the /product/ category listing — it's static
HTML. Card-level extraction (role, company, location, tags). The listing has no
posting date, so for the cards we actually return we fetch each detail page once
and read the posting date from its JobPosting JSON-LD (best-effort).
"""
from __future__ import annotations

import json
import re

import httpx
from bs4 import BeautifulSoup

SALARY_RE = re.compile(r"[$€£][\d,]+k?\s*[-–]\s*[$€£]?[\d,]+k?", re.I)

from adapters import dedupe
from engine.textutils import UA, detect_mode, detect_role

BASE = "https://cryptocurrencyjobs.co"
LIST_URL = f"{BASE}/product/"
TYPES = {"full-time", "part-time", "contract", "internship", "freelance", "temporary"}
MAX_PAGE_SIZE = 50
MAX_PAGES = 10


def _card(li) -> dict:
    links = li.find_all("a", href=True)
    if not links:
        return None
    job = links[0]                                   # first link = the role
    href = job["href"]
    segs = [s for s in href.split("/") if s]
    if len(segs) != 2:                               # not a /<category>/<slug>/ posting
        return None
    role = " ".join(job.get_text(" ").split())
    if not role:
        return None
    category = segs[0].lower()

    company, locs, tags, phase = "Unknown", [], [], "pre"
    for x in links[1:]:
        seg = x["href"].strip("/").lower()
        txt = " ".join(x.get_text(" ").split())
        if x["href"].startswith("/startups/"):
            company = txt or company
            phase = "loc"
            continue
        if phase == "loc":
            if seg in TYPES or seg == category:      # locations end at type/category
                phase = "tags"
                continue
            if txt and not txt.startswith("("):
                locs.append(txt)
        elif phase == "tags" and txt:
            tags.append(txt)

    location = ", ".join(dict.fromkeys(locs))
    tagstr = ", ".join(dict.fromkeys(tags))
    sal = SALARY_RE.search(li.get_text(" "))
    return {
        "role": detect_role(role),
        "title": role,
        "company": company,
        "url": BASE + href,
        "mode": detect_mode(location),
        "location": location,
        "salary": re.sub(r"\s+", " ", sal.group(0)).strip() if sal else "",
        "description": f"{role} at {company}\n{location}\nTags: {tagstr}".strip(),
        "posted": "",
        "source": "Cryptocurrency Jobs",
    }


def _date_from_jsonld(node) -> str:
    """Pull datePosted out of a JobPosting JSON-LD node (dict, list, or @graph)."""
    if isinstance(node, list):
        for n in node:
            d = _date_from_jsonld(n)
            if d:
                return d
        return ""
    if not isinstance(node, dict):
        return ""
    if "@graph" in node:
        return _date_from_jsonld(node["@graph"])
    types = node.get("@type", "")
    types = types if isinstance(types, list) else [types]
    if "JobPosting" in types and node.get("datePosted"):
        return str(node["datePosted"])[:10]
    return ""


DATEPOSTED_RE = re.compile(r'"datePosted"\s*:\s*"([^"]+)"')


def _fetch_posted(url: str, client: httpx.Client) -> str:
    """Best-effort YYYY-MM-DD posting date from a detail page; "" on any failure."""
    try:
        r = client.get(url)
        r.raise_for_status()
        # The JobPosting JSON-LD often has literal newlines in "description", which
        # breaks json.loads — so regex datePosted first, then fall back to parsing.
        m = DATEPOSTED_RE.search(r.text)
        if m:
            return m.group(1)[:10]
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                d = _date_from_jsonld(json.loads(tag.string or ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if d:
                return d
        t = soup.find("time", attrs={"datetime": True})    # fallback
        if t:
            return str(t["datetime"])[:10]
    except Exception:
        pass
    return ""


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2) -> dict:
    r = httpx.get(LIST_URL, headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    cards, seen = [], set()
    for comp in soup.select('a[href^="/startups/"]'):
        li = comp.find_parent("li")
        if not li or id(li) in seen:
            continue
        seen.add(id(li))
        c = _card(li)
        if c:
            cards.append(c)

    kw = keyword.strip().lower()
    if kw:
        cards = [c for c in cards if kw in (c["title"] + " " + c["description"]).lower()]
    if remote_only:
        cards = [c for c in cards if c["mode"] == "remote"]

    cards = dedupe(cards)
    total = len(cards)
    cap = max(1, min(int(page_size), MAX_PAGE_SIZE)) * max(1, min(int(pages), MAX_PAGES))
    out = cards[:cap]

    # enrich only the cards we return with their posting date (one request each)
    with httpx.Client(headers={"User-Agent": UA}, timeout=15.0,
                      follow_redirects=True) as client:
        for c in out:
            c["posted"] = _fetch_posted(c["url"], client)

    return {"total": total, "pages_fetched": 1, "page_size": page_size,
            "direct_url": LIST_URL, "jobs": out}
