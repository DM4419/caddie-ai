"""Browser-tier board: Dynamite Jobs (dynamitejobs.com).

No public API and the search results are client-rendered, so we render the product
search with Playwright and parse the result cards directly. Cards are `div`s with
id `result-item-<id>`; the generic JSON-LD/heuristic extractor misses them (they
carry no <a> link — navigation is JS), hence this dedicated parser.

Card structure (mapped from the live DOM):
  h2                      -> title          p (first)        -> company
  span (class-less)       -> eligibility location(s)         span '$… per …' -> salary
  span '… ago'            -> posting age    div.inline-block -> skill tags
Per-job URLs aren't in the DOM, so we rebuild them from the observed pattern
/company/<company-slug>/remote-job/<title-slug>. Dedup also falls back to
company|title, so an occasional slug miss doesn't create duplicates.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from adapters import dedupe
from engine.textutils import detect_role

BASE = "https://dynamitejobs.com"
LIST_URL = f"{BASE}/remote-jobs/product?text=product"
MAX_PAGE_SIZE = 50
MAX_PAGES = 10
_SALARY_RE = re.compile(r"[$€£]|per\s+(hour|year|month|day|week)", re.I)
_AGO_RE = re.compile(r"(\d+)\s*(day|week|month|year)", re.I)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _clean(el) -> str:
    return " ".join(el.get_text(" ").split()) if el else ""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _posted(text: str) -> str:
    """'Opened 3 days ago' / 'today' -> YYYY-MM-DD (best-effort, '' if unparseable)."""
    t = (text or "").lower()
    if "today" in t or "hour" in t or "minute" in t or "just now" in t:
        return date.today().isoformat()
    m = _AGO_RE.search(t)
    if not m:
        return ""
    days = _UNIT_DAYS[m.group(2)] * int(m.group(1))
    return (date.today() - timedelta(days=days)).isoformat()


def _card_to_job(card) -> dict | None:
    h2 = card.find("h2")
    title = _clean(h2)
    if not title:                                    # ad / non-job card -> skip
        return None
    p = card.find("p")
    company = _clean(p) or "Unknown"

    locs, salary, posted = [], "", ""
    for sp in card.find_all("span"):
        txt = _clean(sp)
        if not txt:
            continue
        if not sp.get("class"):                      # class-less spans hold eligibility locations
            if len(txt) > 1 and txt != ",":
                locs.append(txt)
        elif not salary and _SALARY_RE.search(txt):
            salary = txt
        elif not posted and "ago" in txt.lower():
            posted = _posted(txt)
    location = ", ".join(dict.fromkeys(locs))
    skills = ", ".join(dict.fromkeys(_clean(d) for d in card.select("div.inline-block") if _clean(d)))

    return {
        "role": detect_role(title),
        "title": title,
        "company": company,
        "url": f"{BASE}/company/{_slug(company)}/remote-job/{_slug(title)}",
        "mode": "remote",                            # Dynamite is a remote-only board
        "location": location,
        "salary": salary,
        "description": f"{title} at {company}\n{location}\nSkills: {skills}".strip(),
        "posted": posted,
        "source": "Dynamite Jobs",
    }


def fetch(keyword: str = "", remote_only: bool = False,
          page_size: int = 20, pages: int = 2) -> dict:
    from bs4 import BeautifulSoup

    from adapters import browser
    html = browser.render(LIST_URL)
    soup = BeautifulSoup(html, "html.parser")
    cards = [_card_to_job(c) for c in soup.select('[id^="result-item-"]')]
    jobs = [c for c in cards if c]

    kw = keyword.strip().lower()
    if kw:
        jobs = [j for j in jobs if kw in (j["title"] + " " + j["description"]).lower()]
    if remote_only:
        jobs = [j for j in jobs if j["mode"] == "remote"]

    jobs = dedupe(jobs)
    total = len(jobs)
    cap = max(1, min(int(page_size), MAX_PAGE_SIZE)) * max(1, min(int(pages), MAX_PAGES))
    return {"total": total, "pages_fetched": 1, "page_size": page_size,
            "direct_url": LIST_URL, "jobs": jobs[:cap]}
