"""Browser tier: render JS-heavy / lightly bot-protected boards with Playwright,
then reuse the JSON-LD extractor on the rendered HTML.

Per the project rules this NEVER crawls a full site — it loads the board's own
filtered search URL (one per job-title query) and indexes only those results.
A module-wide rate limiter spaces navigations out (randomised) so we stay polite
and fly under simple rate-based blockers. It will NOT defeat advanced bot walls
(DataDome/PerimeterX, e.g. Glassdoor) — those are out of scope by design.
"""
from __future__ import annotations

import random
import re
import threading
import time
from datetime import date, timedelta
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from adapters import dedupe
from adapters.listing import extract_jobpostings
from engine.textutils import detect_mode, detect_role

_SALARY_RE = re.compile(r"[$€£][\d,.]+\s*k?\s*(?:[-–—]\s*[$€£]?[\d,.]+\s*k?)?(?:/\w+)?", re.I)
_REL_RE = re.compile(r"(\d+)\+?\s*(hour|day|week|month)s?\s+ago", re.I)
_REL_DAYS = {"hour": 0, "day": 1, "week": 7, "month": 30}

# Realistic desktop Chrome on macOS — matches the installed headless build.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

# --- module-wide polite rate limiter -------------------------------------
_lock = threading.Lock()
_last_nav = [0.0]
MIN_GAP = 6.0          # seconds between navigations (base)
JITTER = 5.0           # + up to this many seconds, randomised


def _throttle() -> None:
    with _lock:
        wait = (_last_nav[0] + MIN_GAP + random.uniform(0, JITTER)) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_nav[0] = time.monotonic()


_STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"


def render(url: str, settle_ms: int = 1500) -> str:
    """Return the fully-rendered HTML for one URL (rate-limited)."""
    from playwright.sync_api import sync_playwright
    _throttle()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(
            user_agent=UA, locale="en-GB", timezone_id="Europe/London",
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"})
        ctx.add_init_script(_STEALTH)
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass                         # some boards never go fully idle
            page.mouse.wheel(0, 4000)        # nudge lazy-loaded cards
            page.wait_for_timeout(settle_ms)
            return page.content()
        finally:
            ctx.close()
            browser.close()


_ATS_URL_RE = re.compile(
    r"https?://(?:job-boards|boards)\.greenhouse\.io/[a-z0-9_-]+/jobs/\d+"
    r"|https?://jobs\.lever\.co/[a-z0-9_-]+/[a-z0-9-]+"
    r"|https?://jobs\.ashbyhq\.com/[a-z0-9_-]+/[a-z0-9-]+"
    r"|https?://apply\.workable\.com/[a-z0-9_-]+", re.I)


def resolve_apply(url: str, settle_ms: int = 1500) -> str:
    """Navigate an aggregator/apply link in a REAL browser (gets past bot walls that
    429 httpx) and return the underlying real apply URL — following an embedded ATS
    link or a tokenised 'apply' redirect (e.g. Adzuna's /jobs/land/ad/<id>). '' on fail."""
    from urllib.parse import urljoin

    from playwright.sync_api import sync_playwright
    _throttle()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(
            user_agent=UA, locale="en-GB", timezone_id="Europe/London",
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"})
        ctx.add_init_script(_STEALTH)
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(settle_ms)
            if _ATS_URL_RE.search(page.url):           # already redirected to the ATS
                return page.url
            html = page.content()
            m = _ATS_URL_RE.search(html)               # ATS link embedded in the page
            if m:
                return m.group(0)
            m2 = re.search(r'(/jobs/land/ad/\d+\?[^"\'\s]+)', html)  # aggregator apply redirect
            if m2:
                host = urlparse(page.url).netloc
                page.goto(urljoin(page.url, m2.group(1).replace("&amp;", "&")),
                          wait_until="domcontentloaded", timeout=45000)
                try:                                   # the land page JS-redirects onward
                    page.wait_for_url(lambda u: urlparse(u).netloc != host, timeout=15000)
                except Exception:
                    pass
                page.wait_for_timeout(settle_ms)
                return page.url if urlparse(page.url).netloc != host else ""
            return ""
        except Exception:
            return ""
        finally:
            ctx.close()
            browser.close()


def _source_name(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    return host.split(".")[0].title() if host else "Board"


def _txt(el) -> str:
    return " ".join(el.get_text(" ").split()) if el else ""


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ABS_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?(?:\s+(\d{4}))?\b")       # 13 May [2026]
_ABS_RE2 = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(\d{4}))?\b")  # May 13[, 2026]


def _abs_date(day: int, mon: str, year) -> str:
    m = _MONTHS.get(mon[:3].lower())
    if not m or not (1 <= day <= 31):
        return ""
    try:
        if year:
            return date(int(year), m, day).isoformat()
        d = date(date.today().year, m, day)
        if d > date.today():                       # no year given and it's future -> last year
            d = date(date.today().year - 1, m, day)
        return d.isoformat()
    except ValueError:
        return ""


def _rel_date(text: str) -> str:
    low = text.lower()
    if "today" in low or "just now" in low or "hours ago" in low or "hour ago" in low:
        return date.today().isoformat()
    if "yesterday" in low:
        return (date.today() - timedelta(days=1)).isoformat()
    m = _REL_RE.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return (date.today() - timedelta(days=_REL_DAYS.get(unit, 0) * n)).isoformat()
    m = _ABS_RE.search(text)                        # "13 May [2026]"
    if m:
        d = _abs_date(int(m.group(1)), m.group(2), m.group(3))
        if d:
            return d
    m = _ABS_RE2.search(text)                       # "May 13[, 2026]"
    if m:
        d = _abs_date(int(m.group(2)), m.group(1), m.group(3))
        if d:
            return d
    return ""


def _find(card, *patterns):
    """First descendant whose data-testid / data-qa / class / itemprop matches."""
    rx = re.compile("|".join(patterns), re.I)
    return (card.find(attrs={"data-testid": rx})
            or card.find(attrs={"data-qa": rx})
            or card.find(class_=rx)
            or card.find(attrs={"itemprop": rx}))


# promo badges that pollute the company on aggregators (Reed, Indeed, …)
_BADGE_RE = re.compile(r"\b(promoted|featured|sponsored|new|easy ?apply|ending soon|"
                       r"early ?bird|hot|urgent|top job|premium|verified)\b", re.I)


def _clean_company(txt: str) -> str:
    if not txt:
        return ""
    if re.search(r"\bby\b", txt, re.I):              # "2 days ago by Acme Ltd" -> "Acme Ltd"
        txt = re.split(r"\bby\b", txt, flags=re.I)[-1]
    txt = _BADGE_RE.sub("", txt)
    txt = re.sub(r"\b\d+\s*(day|hour|week|month|min(ute)?)s?\s*ago\b", "", txt, flags=re.I)
    return re.sub(r"\s+", " ", txt).strip(" ·-–|")


# premium / paywall CTA text that masquerades as a title on some boards
_CTA_RE = re.compile(r"unlock|sign ?up|log ?in|see (all|more)|view (all|details)|"
                     r"register|subscribe|go premium", re.I)
_FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")    # regional-indicator flags
_PLACE_RE = re.compile(r"\b(remote|hybrid|on-?site|anywhere|worldwide|"
                       r"emea|europe|americas?|apac|usa?|uk|united)\b", re.I)


def _card_company(card, head) -> str:
    """Company via class/qa hint (incl. 'posted by'), else sibling text. Strips
    promo badges and 'N days ago by …' noise."""
    el = _find(card, r"company|employer|organi[sz]ation|brand|recruiter|"
               r"advertiser|posted.?by|hiring")
    if el and _txt(el):
        return _clean_company(_txt(el))
    if el:                                          # logo-only block -> img alt
        img = el.find("img", alt=True)
        if img:
            return _clean_company(re.sub(r"\s*(logo|company)\s*$", "", img["alt"], flags=re.I))
    if head and head.parent:                        # text beside the title (e.g. "• N26")
        for ch in head.parent.find_all(recursive=False):
            t = _txt(ch)
            if ch is not head and t and t not in ("•", "·", "-", "—", "|", "–") and not _BADGE_RE.fullmatch(t.strip()):
                return _clean_company(t)
    return ""


def _card_location(card) -> str:
    el = _find(card, r"location|place|region|country|geo")
    if el and _txt(el):
        return _txt(el)
    # else: a tag/bubble/pill that looks like a place (flag emoji or place word)
    for b in card.find_all(class_=re.compile(r"bubble|tag|pill|chip|badge|label", re.I)):
        t = _txt(b)
        if t and (_FLAG_RE.search(t) or _PLACE_RE.search(t)):
            return _FLAG_RE.sub("", t).strip()
    return ""


_JOBHREF_RE = re.compile(r"/(remote-jobs|jobs?|listing|position|vacanc|career|"
                         r"opening|role|stell[e]|companies|internship)\b", re.I)


def _best_group(soup) -> list:
    """Generic fallback: find the largest group of sibling containers that each
    hold a distinct link + heading — i.e. the repeated job-card structure, no
    matter what the board calls its classes."""
    from collections import defaultdict
    groups = defaultdict(list)
    for el in soup.find_all(["li", "article", "div"]):
        a = el.find("a", href=True)
        if not a or len(el.get_text(strip=True)) < 15:
            continue
        groups[(el.name, tuple(el.get("class") or []))].append(el)
    best, best_score = [], 0.0
    for els in groups.values():
        if len(els) < 3:
            continue
        hrefs, headed, jobish, depth = set(), 0, 0, 0
        for e in els:
            a = e.find("a", href=True)
            if a:
                hrefs.add(a["href"])
                if _JOBHREF_RE.search(a["href"]):
                    jobish += 1
                depth += len([s for s in urlparse(a["href"]).path.split("/") if s])
            if e.find(["h1", "h2", "h3", "h4"]) or e.find(class_=re.compile("title", re.I)):
                headed += 1
        n = len(els)
        # reject nav/category lists: their links are shallow (/marketing/) and
        # rarely job-detail; require some job-looking links or headings
        if jobish / n < 0.3 and headed / n < 0.5:
            continue
        if depth / n < 1.5:                      # single-segment paths = category nav
            continue
        score = (len(hrefs) / n) * n * (0.2 + 1.6 * jobish / n + 0.6 * headed / n)
        if score > best_score:
            best, best_score = els, score
    return best


def heuristic_jobs(html: str, base_url: str, source: str) -> list:
    """DOM-card extraction for boards with no JobPosting JSON-LD (e.g. Joblift).

    Generic: find repeated job-card containers, then pull title / company /
    location / link / posted from common testid/class/heading conventions.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    cards = soup.select('[data-testid="jobItem"]')
    if not cards:
        cards = soup.find_all(attrs={"data-testid": re.compile(r"job.?(item|card|result)", re.I)})
    if not cards:
        cards = soup.find_all(attrs={"data-qa": re.compile(r"^job.?card$|job.?result", re.I)})
    if not cards:
        cards = soup.find_all(["a", "article", "li", "div"],
                              class_=re.compile(r"job.?card|jobcard|job.?item|jobitem|"
                                                r"vacancy|result.?card|search.?result|"
                                                r"bjs-jlid|job.?listing|listing.?item|"
                                                r"job.?result|job.?desktop|posting", re.I))
    # keep only the OUTERMOST matched card (class regex also hits sub-parts like
    # "job-card-left"); drop any card nested inside another matched card
    ids = set(map(id, cards))
    cards = [c for c in cards if not any(id(p) in ids for p in c.parents)]
    if len(cards) < 3:                       # no obvious card class -> generic detect
        cards = _best_group(soup)
    base_path = unquote(urlparse(base_url).path).rstrip("/")
    jobs, seen = [], set()
    for c in cards:
        # skip cards explicitly marked filled/expired (e.g. intelligentpeople.co.uk's
        # `div.expired--message`: "this job is now filled and no longer available")
        if c.find(class_=re.compile(r"expired", re.I)) or re.search(
                r"no longer available|now filled|position (?:has been )?filled|"
                r"this (?:job|position|role|vacancy) (?:has |is )?(?:expired|closed|filled)",
                _txt(c), re.I):
            continue
        # first title/heading whose text isn't a premium "Unlock…" style CTA
        cands = (c.find_all(class_=re.compile(r"job.?title|title", re.I))
                 + c.find_all(["h1", "h2", "h3", "h4"]))
        head = next((e for e in cands if _txt(e) and not _CTA_RE.search(_txt(e))), None)
        # the card itself may be the <a>; else prefer a job-detail anchor over
        # e.g. a company-logo link, falling back to the first link
        if c.name == "a" and c.has_attr("href"):
            link = c
        else:
            anchors = c.find_all("a", href=True)
            link = next((a for a in anchors if _JOBHREF_RE.search(a["href"])),
                        anchors[0] if anchors else None)
        title = _txt(head) or (_txt(link) if link else "")
        if not title:
            continue
        url = urljoin(base_url, link["href"]) if link else ""
        # self-referential tracking links (same path as the search) aren't real
        # per-job URLs and would collapse under url_key — drop them
        if url and unquote(urlparse(url).path).rstrip("/") == base_path:
            url = ""
        company = _card_company(c, head) or source
        location = _card_location(c)
        ctext = _txt(c)
        sal = _SALARY_RE.search(ctext)
        jobs.append({
            "role": detect_role(title), "title": title, "company": company,
            "url": url, "location": location,
            "mode": detect_mode(f"{location}\n{title}"),
            "salary": re.sub(r"\s+", " ", sal.group(0)).strip() if sal else "",
            "description": f"{title} at {company}\n{location}".strip(),
            "posted": _rel_date(ctext), "source": source,
        })
        key = (url or company + "|" + title).lower()
        if key in seen:
            jobs.pop()
        else:
            seen.add(key)
    # if this board gives real per-job links, keep only those — it drops page
    # chrome / article sections that happened to match a card class
    withurl = [j for j in jobs if j["url"]]
    if len(withurl) >= max(3, 0.3 * len(jobs)):
        jobs = withurl
    return jobs


class _BrowserFetcher:
    """Adapts a browser board (base url + optional {q} search template) to the
    board-module .fetch() interface. With a template it's QUERY_BASED so
    board_fetch fans out over the role-title queries (one render each); without
    one it renders the single given URL once."""

    def __init__(self, url: str, search_template: str = "", sep: str = "%20"):
        self.url = url
        self.template = search_template or ""
        self.sep = sep or "%20"
        self.QUERY_BASED = bool(self.template)

    def _url_for(self, keyword: str) -> str:
        kw = keyword.strip()
        if self.template and "{q}" in self.template and kw:
            value = (self.sep or "%20").join(quote(w) for w in kw.split())
            return self.template.replace("{q}", value)
        return self.url

    def fetch(self, keyword: str = "", remote_only: bool = False,
              page_size: int = 50, pages: int = 1) -> dict:
        target = self._url_for(keyword)
        source = _source_name(self.url)
        html = render(target)
        # prefer structured data; fall back to DOM-card heuristics
        jobs = extract_jobpostings(html, source=source, base_url=target)
        if not jobs:
            jobs = heuristic_jobs(html, target, source)
        # NB: don't re-filter by the keyword — the board's own search already
        # scoped it (often fuzzily); the role gate enforces relevance downstream.
        if remote_only:
            jobs = [j for j in jobs if j["mode"] == "remote"]
        jobs = dedupe(jobs)
        return {"jobs": jobs, "total": len(jobs), "pages_fetched": 1,
                "page_size": page_size, "direct_url": target}


def board_fetcher(url: str, search_template: str = "", sep: str = "%20") -> _BrowserFetcher:
    return _BrowserFetcher(url, search_template, sep)


def make_search_template(url: str, role_queries: list):
    """Turn a filtered search URL into a ({q}-template, separator) pair by locating
    the role phrase however the board encoded the spaces between its words —
    %20 (Joblift/GulfTalent), '+' (remote.com), '-' (Bayt), or a literal space.
    Returns ("", "") if no known role phrase is found."""
    seps = ["%20", "+", "-", " "]
    low = url.lower()
    for q in sorted([q for q in role_queries if q], key=len, reverse=True):
        words = q.lower().split()
        for sep in seps:
            enc = sep.join(words)
            i = low.find(enc)
            if i >= 0 and enc:
                return url[:i] + "{q}" + url[i + len(enc):], sep
    return "", ""
