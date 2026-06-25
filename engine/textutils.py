"""Shared text helpers used by both the generic fetcher and the API adapters."""
from __future__ import annotations

import html as _html
import re

from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Role keywords -> normalized title, most specific first.
_ROLE_PATTERNS = [
    (r"chief product officer|\bcpo\b", "Chief Product Officer"),
    (r"entrepreneur in residence|founder[\s-]?in[\s-]?residence|\beir\b", "Entrepreneur in Residence"),
    (r"vp\s+(of\s+)?product", "VP Product"),
    (r"head of product", "Head of Product"),
    (r"director of product|product director", "Director of Product"),
    (r"founding (product manager|pm)", "Founding PM"),
    (r"group product manager", "Group Product Manager"),
    (r"lead product manager", "Lead Product Manager"),
    (r"principal product manager", "Principal Product Manager"),
    (r"senior product manager|\bsr\.?\s+pm\b", "Senior Product Manager"),
    (r"product manager|\bpm\b", "Product Manager"),
]


def detect_mode(text: str) -> str:
    """remote | hybrid | onsite from free text. Defaults to onsite when there's
    no explicit remote signal — most jobs are onsite unless they say otherwise,
    and defaulting to remote falsely inflated the remote score for everything."""
    t = (text or "").lower()
    if "hybrid" in t:
        return "hybrid"
    if re.search(r"\bremote\b|work from home|fully distributed|remote-first", t):
        return "remote"
    return "onsite"


def normalize_mode(explicit: str = "", text: str = "") -> str:
    """Prefer a board's explicit workplace field; fall back to text sniffing."""
    if explicit:
        e = explicit.lower().replace("-", "").replace("_", "").replace(" ", "")
        if "remote" in e:
            return "remote"
        if "hybrid" in e:
            return "hybrid"
        if "onsite" in e or "inoffice" in e or "inperson" in e:
            return "onsite"
    return detect_mode(text)


def detect_role(text: str) -> str:
    """Normalize a job TITLE to a PM category, or return the title itself.

    Only collapses to a canonical PM label when a PM pattern matches; otherwise
    returns the original title (so non-PM roles stay distinct and honest rather
    than all defaulting to "Product Manager"). Long inputs (a whole JD, not a
    title) fall back to a generic label.
    """
    t = (text or "").lower()
    s = (text or "").strip()
    # keep disqualifying qualifiers visible (don't collapse "Technical PM" -> "Product Manager")
    if re.search(r"technical (product|program) manager|technical pm|engineering product manager", t):
        return s if 0 < len(s) <= 80 else "Technical Product Manager"
    for pat, label in _ROLE_PATTERNS:
        if re.search(pat, t):
            return label
    return s if 0 < len(s) <= 80 else "Other role"


_US_RE = re.compile(
    r"\b(usa|u\.?s\.?a?\.?|united states|america|american|new york|san francisco|"
    r"los angeles|chicago|boston|seattle|austin|miami|denver|atlanta|dallas|"
    r"houston|philadelphia|phoenix|san diego|portland|nashville|silicon valley|"
    r"bay area|nyc|brooklyn|manhattan|\bsf\b|washington dc|d\.c\.)\b"
    r"|\bus\b|,\s*(ny|ca|tx|fl|ma|il|co|ga|va|nc|wa|az|pa|oh|mi|nj|md|mn|or|ut|tn)\b",
    re.I)


def is_us(location: str) -> bool:
    """True if a location string looks US-based."""
    return bool(_US_RE.search(location or ""))


_AMERICAS_RE = re.compile(
    r"\b(americas?|latin america|latam|north america|south america|central america|"
    r"canada|toronto|vancouver|montreal|ottawa|calgary|"
    r"mexico|brazil|brasil|sao paulo|argentina|buenos aires|bogota|colombia|"
    r"chile|santiago|lima|peru)\b",
    re.I)


def is_americas(location: str) -> bool:
    return bool(_AMERICAS_RE.search(location or ""))


# On-site/hybrid in these regions is a hard NO for a UK-based candidate (not relocating).
_FAR_GEO_RE = re.compile(
    r"\b(bangalore|bengaluru|mumbai|delhi|hyderabad|pune|chennai|gurgaon|gurugram|noida|kolkata|india|"
    r"dubai|abu dhabi|uae|qatar|doha|riyadh|jeddah|saudi|bahrain|kuwait|\boman\b|"
    r"singapore|hong kong|tokyo|osaka|japan|seoul|korea|"
    r"shanghai|shenzhen|beijing|guangzhou|china|taiwan|taipei|"
    r"sydney|melbourne|australia|auckland|new zealand|"
    r"bangkok|thailand|jakarta|indonesia|manila|philippines|kuala lumpur|malaysia|vietnam|hanoi)\b",
    re.I)


def is_far_geo(location: str) -> bool:
    """A clearly out-of-region location (India/MENA/APAC) the user won't relocate to."""
    return bool(_FAR_GEO_RE.search(location or ""))


# Signals that a remote role is open to a region the user CAN take, even if it
# also lists US/Americas (e.g. "Remote - Americas, EMEA" is fine).
_FRIENDLY_RE = re.compile(
    r"\b(emea|europe|european|eu|uk|united kingdom|britain|england|london|"
    r"ireland|germany|france|spain|portugal|netherlands|poland|global|"
    r"worldwide|world.?wide|anywhere|international|apac|mena)\b", re.I)


def is_remote_friendly(location: str) -> bool:
    """True if a remote location includes a region the user can work from."""
    return bool(_FRIENDLY_RE.search(location or ""))


_FULLY_REMOTE_RE = re.compile(
    r"\bfully[\s-]remote\b|\bwork\s+from\s+anywhere\b|\bremote[\s,–-]*anywhere\b|"
    r"\b100%\s*remote\b|\bfully\s+distributed\b|\bremote\s*\(?\s*(global|worldwide)\)?\b|"
    r"\banywhere\s+in\s+the\s+world\b|\bglobally\s+remote\b|\bremote[\s-]first\b", re.I)


def is_fully_remote(text: str) -> bool:
    """True if the text signals a location-independent (work-from-anywhere) role."""
    return bool(_FULLY_REMOTE_RE.search(text or ""))


# US detector tuned for free-text JD bodies (NOT location strings): drops the bare
# "us"/"america"/state-abbreviation matches that trip on prose ("join us", "US
# market"), keeping strong, unambiguous markers of a US-listed/US-eligible role.
_US_TEXT_RE = re.compile(
    r"\b(u\.?s\.?a\.?|united states)\b|"
    r"\bUS[-\s]based\b|\b(?:in|within|across|located in|reside in) the U\.?S\.?\b|"
    r"authoriz(?:ed|ation) to work in the (?:united states|u\.?s\.?)\b|"
    r"\b(new york|san francisco|los angeles|chicago|boston|seattle|austin|miami|"
    r"denver|atlanta|dallas|houston|philadelphia|phoenix|san diego|portland|"
    r"nashville|silicon valley|bay area|nyc|brooklyn|manhattan)\b",
    re.I)
# An explicit offer of a region the user CAN work from (keeps a US-listed role only
# if it genuinely also opens up UK/EU — NOT mere 'anywhere in the world' boilerplate).
_ALLOWED_REGION_RE = re.compile(
    r"\b(uk|united kingdom|britain|england|ireland|emea|europe|european|\beu\b|"
    r"germany|france|spain|portugal|netherlands|poland|nordics|dach)\b", re.I)


def looks_us_only(text: str) -> bool:
    """True when a JD body shows the role's stated location/eligibility is US/Americas.
    Scans the header region (where boards put country/eligibility) so a US role is
    caught even when its location field is blank/'Remote'. 'Work from anywhere in the
    world' marketing does NOT keep it; only an explicit UK/EU offer does."""
    head = (text or "")[:900]
    if not (_US_TEXT_RE.search(head) or is_americas(head)):
        return False
    return not bool(_ALLOWED_REGION_RE.search(head))


# --- coarse UTC-offset classifier, for the timezone gate ----------------------
# Standard (non-DST) offsets by region keyword; ordered most-specific first so an
# in-band Gulf/Caucasus city isn't swallowed by a broad Asia pattern. Enough
# granularity to keep/drop by a collaboration band (e.g. CET-1..CET+4 = UTC 0..5).
_TZ_BUCKETS = [
    (re.compile(r"\b(bangladesh|dhaka|thailand|bangkok|vietnam|hanoi|ho chi minh|"
                r"indonesia|jakarta|china|shanghai|shenzhen|beijing|guangzhou|"
                r"singapore|hong kong|malaysia|kuala lumpur|philippines|manila|"
                r"taiwan|taipei|japan|tokyo|osaka|korea|seoul|"
                r"australia|sydney|melbourne|brisbane|perth|new zealand|auckland)\b", re.I), 8.0),
    (re.compile(r"\b(india|bengaluru|bangalore|mumbai|new delhi|delhi|hyderabad|pune|"
                r"chennai|gurgaon|gurugram|noida|kolkata|sri lanka|colombo)\b", re.I), 5.5),
    (re.compile(r"\b(pakistan|karachi|lahore|islamabad|uzbekistan|tashkent|maldives|"
                r"turkmenistan|ashgabat)\b", re.I), 5.0),
    (re.compile(r"\b(uae|u\.a\.e\.|dubai|abu dhabi|sharjah|\boman\b|muscat|armenia|yerevan|"
                r"georgia|tbilisi|azerbaijan|baku|mauritius|réunion|reunion)\b", re.I), 4.0),
    (re.compile(r"\b(turkey|türkiye|istanbul|ankara|moscow|saudi|riyadh|jeddah|qatar|doha|"
                r"kuwait|bahrain|manama|iraq|baghdad|kenya|nairobi|ethiopia|addis|"
                r"tanzania|dar es salaam|uganda|kampala)\b", re.I), 3.0),
    (re.compile(r"\b(finland|helsinki|greece|athens|romania|bucharest|bulgaria|sofia|"
                r"ukraine|kyiv|kiev|estonia|tallinn|latvia|riga|lithuania|vilnius|"
                r"cyprus|moldova|chisinau|israel|tel aviv|jerusalem|egypt|cairo|"
                r"south africa|johannesburg|cape town|pretoria|durban)\b", re.I), 2.0),
    # Catch-all for recognised UK / Western+Central Europe / West+Central Africa / Morocco.
    (re.compile(r"\b(uk|united kingdom|britain|england|london|manchester|scotland|wales|"
                r"ireland|dublin|portugal|lisbon|porto|iceland|reykjavik|morocco|"
                r"spain|madrid|barcelona|france|paris|germany|berlin|munich|hamburg|"
                r"italy|rome|milan|netherlands|amsterdam|belgium|brussels|luxembourg|"
                r"switzerland|zurich|geneva|austria|vienna|poland|warsaw|krakow|"
                r"czech|prague|slovakia|hungary|budapest|slovenia|ljubljana|croatia|zagreb|"
                r"serbia|belgrade|sweden|stockholm|norway|oslo|denmark|copenhagen|malta|"
                r"nigeria|lagos|ghana|accra|algeria|tunisia|emea|europe|european|\beu\b)\b", re.I), 1.0),
]


def region_utc_offsets(location: str) -> list:
    """All standard UTC offsets (hours) implied by a location string — a multi-region
    remote role (e.g. 'EMEA | LatAm') yields several. Empty if no region is named.
    Americas/US -> -5. Callers keep a role if ANY offset lands in their band."""
    s = location or ""
    if not s.strip():
        return []
    offs = []
    if is_us(s) or is_americas(s):
        offs.append(-5.0)
    for rx, off in _TZ_BUCKETS:
        if rx.search(s):
            offs.append(off)
    return offs


def region_utc_offset(location: str):
    """Single representative offset (the one nearest UTC+1/CET among matches), or None."""
    offs = region_utc_offsets(location)
    return min(offs, key=lambda o: abs(o - 1.0)) if offs else None


def fmt_salary(mn, mx, currency: str = "", predicted: bool = False) -> str:
    """Format a min/max salary into a compact range like '£140k–165k' (or '')."""
    def k(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        if v <= 0:
            return None
        return f"{int(round(v / 1000))}k" if v >= 1000 else str(int(v))
    a, b = k(mn), k(mx)
    sym = {"GBP": "£", "USD": "$", "EUR": "€"}.get((currency or "").upper(), "")
    if a and b and a != b:
        s = f"{sym}{a}–{b}"
    elif a or b:
        s = f"{sym}{a or b}"
    else:
        return ""
    return ("~" + s) if predicted else s


def strip_html(s: str) -> str:
    """HTML (possibly entity-encoded, e.g. Greenhouse) -> clean plain text."""
    if not s:
        return ""
    s = _html.unescape(s)
    text = BeautifulSoup(s, "html.parser").get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
