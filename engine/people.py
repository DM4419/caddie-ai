"""Shortlist people to reach out to on LinkedIn for a target company.

Search engines block server-side scraping (Google and DuckDuckGo serve captchas),
so by default this builds ready-made Google + LinkedIn **search links** you open in
your own logged-in browser — where you see real names, roles and recent posts. If a
Brave Search API key is set (BRAVE_API_KEY, free tier at https://brave.com/search/api/),
it also lists matching LinkedIn profiles (name + role) inline.
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote_plus

# Decision-makers / useful contacts to target: (label, Google title-terms, LinkedIn keyword).
PERSONAS = [
    ("Hiring manager / product lead",
     '("head of product" OR "director of product" OR "VP product" OR "product lead")',
     "Head of Product"),
    ("Recruiter / talent",
     '("recruiter" OR "talent acquisition" OR "talent partner" OR "people team")',
     "Recruiter"),
    ("Founder / CEO",
     '("founder" OR "co-founder" OR "CEO" OR "CPO")',
     "Founder"),
    ("Product peer",
     '("product manager" OR "senior product manager" OR "group product manager")',
     "Product Manager"),
]


def has_search_api() -> bool:
    return bool(os.environ.get("BRAVE_API_KEY"))


def _google(q: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(q)


def _ddg(q: str) -> str:
    return "https://duckduckgo.com/?q=" + quote_plus(q)


def _linkedin(keywords: str) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(keywords)


def brave_people(query: str, limit: int = 5) -> list:
    """LinkedIn profiles for a query via the Brave Search API (name + role), or []
    if no key / blocked. Brave is API-based (no scraping) and returns clean results."""
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return []
    try:
        import httpx
        r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                      params={"q": query, "count": 12},
                      headers={"X-Subscription-Token": key, "Accept": "application/json"},
                      timeout=15)
        results = (r.json().get("web", {}) or {}).get("results", []) or []
    except Exception:
        return []
    people, seen = [], set()
    for w in results:
        url = (w.get("url") or "").split("?")[0]
        if "linkedin.com/in" not in url or url in seen:
            continue
        seen.add(url)
        # LinkedIn result titles are usually "Name - Title - Company | LinkedIn"
        title = (w.get("title") or "").split(" | ")[0]
        parts = [p.strip() for p in title.split(" - ")]
        name = parts[0] if parts else title
        role = parts[1] if len(parts) > 1 else ""
        people.append({"name": name, "title": role, "url": url})
        if len(people) >= limit:
            break
    return people


def targets(company: str, role: str = "") -> list:
    """Per-persona search links (+ inline people if a search API key is set)."""
    company = (company or "").strip()
    out = []
    for label, terms, li_kw in PERSONAS:
        gq = f'site:linkedin.com/in {terms} "{company}"'
        out.append({
            "label": label,
            "query": gq,
            "google": _google(gq),
            "ddg": _ddg(gq),
            "linkedin": _linkedin(f"{li_kw} {company}"),
            "people": brave_people(gq),
        })
    return out


def connection_note(company: str, role: str = "", summary: str = "") -> str:
    """A short, grounded LinkedIn connection-note draft ([Name] placeholder)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return ""
    try:
        import anthropic
        from engine.draft import DEFAULT_MODEL
        client = anthropic.Anthropic(api_key=key)
        system = (
            "Write a SHORT LinkedIn connection note (<= 300 characters) from a candidate to "
            "someone at a target company. Warm, specific, low-key — no flattery clichés, no "
            "'I'd love to pick your brain'. British spelling, no em-dashes. Address the recipient "
            "as [Name]. Reference the company and the candidate's genuinely relevant angle. NEVER "
            "invent facts about the company or person. Output ONLY the note text — never a "
            "question, apology, or commentary. If details are thin, still write a brief, warm, "
            "generic note using [Name] and the company name.")
        user = (f"Company: {company}\nRole I'm interested in: {role}\n"
                f"My background: {(summary or '').strip()[:700]}")
        msg = client.messages.create(model=DEFAULT_MODEL, max_tokens=220, system=system,
                                     messages=[{"role": "user", "content": user}])
        out = "".join(b.text for b in msg.content if b.type == "text").strip().strip('"')
        # guard: drop a conversational refusal rather than surface it as the "note"
        if re.match(r"(?i)^(i need|i'?d need|could you|it looks like|please (provide|paste)|"
                    r"i don'?t have|sure[,!])\b", out):
            return ""
        return out
    except Exception:
        return ""
