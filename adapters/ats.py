"""Generic ATS board adapter — scan ALL open roles at a company's careers board.

Where adapters/api.py turns a single ATS *job* URL into one Job, this lists the
whole board (Greenhouse / Lever / Ashby) given a company slug, so a user can add
"a company's careers page" and have every Product role surface. The downstream
role gate (filter_target_roles) narrows the full list to PM-type roles.

A board entry created from the UI carries {provider, slug}; `board_fetcher()`
wraps those into the same .fetch() shape the static board modules expose.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from engine.textutils import (UA, detect_mode, detect_role, normalize_mode,
                              strip_html)

TIMEOUT = 20.0
PROVIDERS = ("greenhouse", "lever", "ashby", "recruitee", "smartrecruiters",
             "workable", "personio")


def _get(url: str, **kw) -> httpx.Response:
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                  follow_redirects=True, **kw)
    r.raise_for_status()
    return r


def _slug_company(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


# Patterns to pull a {provider, slug} out of a careers/ATS URL or page HTML.
_SLUG_RES = {
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I),
    "ashby": re.compile(r"(?:jobs\.ashbyhq\.com|ashbyhq\.com/job-board)/([a-z0-9_-]+)", re.I),
    "smartrecruiters": re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([a-z0-9_-]+)", re.I),
    "workable": re.compile(r"apply\.workable\.com/([a-z0-9_-]+)", re.I),
}
# subdomain-form providers: <slug>.<host>
_SUBDOMAIN_HOSTS = {
    "recruitee.com": "recruitee",
    "jobs.personio.com": "personio", "jobs.personio.de": "personio",
    "workable.com": "workable",
}
_GH_FOR_RE = re.compile(r"greenhouse\.io/embed/job_board\?for=([a-z0-9_-]+)", re.I)


def parse_careers_url(url: str) -> dict | None:
    """Return {provider, slug} for an ATS careers URL, or None.

    Handles direct ATS hosts (boards.greenhouse.io/<slug>, jobs.lever.co/<slug>,
    jobs.ashbyhq.com/<slug>, <slug>.greenhouse.io) and, as a fallback, a company
    careers page that EMBEDS one of those boards (we fetch and scan the HTML).
    """
    if not (url or "").strip():
        return None
    host = urlparse(url).netloc.lower()
    # <slug>.greenhouse.io subdomain form
    if host.endswith(".greenhouse.io"):
        sub = host.split(".")[0]
        if sub not in ("boards", "job-boards", "boards-api", "www"):
            return {"provider": "greenhouse", "slug": sub}
    # <slug>.<host> subdomain providers (Personio / Recruitee / Workable)
    for suffix, prov in _SUBDOMAIN_HOSTS.items():
        if host.endswith("." + suffix):
            sub = host[: -len(suffix) - 1].split(".")[0]
            if sub and sub not in ("www", "apply", "jobs", "careers"):
                return {"provider": prov, "slug": sub}
    for prov, rx in _SLUG_RES.items():
        m = rx.search(url)
        if m:
            return {"provider": prov, "slug": m.group(1)}
    # fallback: a company careers page may EMBED its ATS board. Only trust this
    # when the page references exactly ONE ATS board — an aggregator (remote.com,
    # job search sites) mentions many companies' boards and must not be matched.
    try:
        html = _get(url).text
    except Exception:
        return None
    found = set()
    for m in _GH_FOR_RE.finditer(html):
        found.add(("greenhouse", m.group(1)))
    for prov, rx in _SLUG_RES.items():
        for m in rx.finditer(html):
            found.add((prov, m.group(1)))
    if len(found) == 1:
        prov, slug = next(iter(found))
        return {"provider": prov, "slug": slug}
    return None


# ---- per-provider board listings -> normalized raw dicts -----------------
def _raw(title, company, url, location, mode, description, posted) -> dict:
    return {"role": detect_role(title), "title": title, "company": company,
            "url": url, "location": location, "mode": mode,
            "description": description.strip(), "posted": (posted or "")[:10],
            "source": company}


def _list_greenhouse(slug: str) -> list:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true").json()
    company = _slug_company(slug)
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name", "")
        body = strip_html(j.get("content", ""))
        # Greenhouse's absolute_url often hides the id in a query string
        # (…/jobs/search?gh_jid=N), which dedupe's url_key drops — so all roles
        # collapse to one. Use the canonical board URL (id in the path) instead.
        jid = j.get("id")
        url = f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}" if jid \
            else j.get("absolute_url", "")
        out.append(_raw(title, company, url, loc,
                        detect_mode(f"{loc}\n{body}"),
                        f"{title}\n{loc}\n\n{body}", j.get("updated_at", "")))
    return out


def _list_lever(slug: str) -> list:
    data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json").json()
    company = _slug_company(slug)
    out = []
    for j in data:
        title = j.get("text", "")
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        meta = " · ".join(filter(None, [loc, cats.get("department"),
                                         cats.get("team"), cats.get("commitment")]))
        body = j.get("descriptionPlain") or strip_html(j.get("description", ""))
        posted = ""
        if j.get("createdAt"):
            try:
                from datetime import datetime, timezone
                posted = datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).date().isoformat()
            except Exception:
                posted = ""
        out.append(_raw(title, company, j.get("hostedUrl", ""), loc,
                        normalize_mode(j.get("workplaceType", ""), f"{loc}\n{body}"),
                        f"{title}\n{meta}\n\n{body}", posted))
    return out


def _list_ashby(slug: str) -> list:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true").json()
    company = _slug_company(slug)
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        loc = j.get("location", "") or ""
        body = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", ""))
        explicit = j.get("workplaceType") or ("remote" if j.get("isRemote") else "")
        out.append(_raw(title, company, j.get("jobUrl", ""), loc,
                        normalize_mode(explicit, f"{loc}\n{body}"),
                        f"{title}\n{loc}\n\n{body}", j.get("publishedAt", "")))
    return out


# ---- Recruitee -----------------------------------------------------------
def _list_recruitee(slug: str) -> list:
    data = _get(f"https://{slug}.recruitee.com/api/offers/").json()
    company = _slug_company(slug)
    out = []
    for o in data.get("offers", []):
        title = o.get("title", "")
        loc = ", ".join(filter(None, [o.get("city"), o.get("country")])) or o.get("location", "")
        body = strip_html(o.get("description", ""))
        mode = "remote" if o.get("remote") else detect_mode(f"{loc}\n{body}")
        out.append(_raw(title, company, o.get("careers_url") or o.get("careers_apply_url", ""),
                        loc, mode, f"{title}\n{loc}\n\n{body}", o.get("published_at", "")))
    return out


# ---- SmartRecruiters -----------------------------------------------------
def _list_smartrecruiters(slug: str) -> list:
    data = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100").json()
    out = []
    for p in data.get("content", []):
        title = p.get("name", "")
        loc = p.get("location", {}) or {}
        locstr = ", ".join(filter(None, [loc.get("city"), loc.get("country")]))
        company = (p.get("company", {}) or {}).get("name") or _slug_company(slug)
        mode = "remote" if loc.get("remote") else detect_mode(locstr)
        url = f"https://jobs.smartrecruiters.com/{slug}/{p.get('id', '')}"
        out.append(_raw(title, company, url, locstr, mode,
                        f"{title}\n{locstr}", p.get("releasedDate", "")))
    return out


# ---- Workable (public widget) --------------------------------------------
def _list_workable(slug: str) -> list:
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true").json()
    company = data.get("name") or _slug_company(slug)
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        loc = ", ".join(filter(None, [j.get("city"), j.get("country")])) or j.get("location", "")
        mode = "remote" if j.get("telecommuting") else detect_mode(loc)
        url = j.get("url") or j.get("shortlink") or j.get("application_url", "")
        out.append(_raw(title, company, url, loc, mode, f"{title}\n{loc}",
                        j.get("published_on") or j.get("created_at", "")))
    return out


# ---- Personio (public XML feed) ------------------------------------------
def _list_personio(slug: str) -> list:
    import xml.etree.ElementTree as ET
    last = None
    for tld in ("com", "de"):
        base = f"https://{slug}.jobs.personio.{tld}"
        try:
            root = ET.fromstring(_get(f"{base}/xml").content)
        except Exception as e:
            last = e
            continue
        out = []
        for pos in root.iter("position"):
            def _t(tag):
                e = pos.find(tag)
                return (e.text or "").strip() if e is not None and e.text else ""
            title = _t("name")
            if not title:
                continue
            office = _t("office")
            body = ""
            jd = pos.find("jobDescriptions")
            if jd is not None:
                body = "\n".join(strip_html((d.findtext("value") or "")) for d in jd.iter("jobDescription"))
            pid = _t("id")
            out.append(_raw(title, _slug_company(slug), f"{base}/job/{pid}" if pid else base,
                            office, detect_mode(f"{office}\n{body}"),
                            f"{title}\n{office}\n\n{body}", _t("createdAt")))
        return out
    raise (last or ValueError("personio board not found"))


_LISTERS = {"greenhouse": _list_greenhouse, "lever": _list_lever, "ashby": _list_ashby,
            "recruitee": _list_recruitee, "smartrecruiters": _list_smartrecruiters,
            "workable": _list_workable, "personio": _list_personio}


def list_board(provider: str, slug: str) -> list:
    lister = _LISTERS.get(provider)
    if not lister:
        raise ValueError(f"unsupported ATS provider: {provider}")
    return lister(slug)


class _BoardFetcher:
    """Adapts an ATS {provider, slug} to the board-module .fetch() interface."""
    QUERY_BASED = False

    def __init__(self, provider: str, slug: str):
        self.provider, self.slug = provider, slug

    def fetch(self, keyword: str = "", remote_only: bool = False,
              page_size: int = 50, pages: int = 1) -> dict:
        from adapters import dedupe
        jobs = list_board(self.provider, self.slug)
        kw = keyword.strip().lower()
        if kw:
            jobs = [j for j in jobs if kw in (j["title"] + " " + j["description"]).lower()]
        if remote_only:
            jobs = [j for j in jobs if j["mode"] == "remote"]
        jobs = dedupe(jobs)
        direct = f"https://{self.provider}.io/{self.slug}" if self.provider == "greenhouse" \
            else f"https://jobs.{self.provider}.{'co' if self.provider=='lever' else 'com'}/{self.slug}"
        return {"jobs": jobs, "total": len(jobs), "pages_fetched": 1,
                "page_size": page_size, "direct_url": direct}


def board_fetcher(provider: str, slug: str) -> _BoardFetcher:
    return _BoardFetcher(provider, slug)
