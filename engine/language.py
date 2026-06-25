"""Detect spoken-language requirements in a JD.

Two outputs the scorer cares about:
- `blocked`: the JD requires fluent/native proficiency in a language the
  candidate does NOT speak, with no spoken language offered as an alternative
  (so "fluent in English or German" is fine — English satisfies it).
- `boost`: a language the candidate wants credit for (e.g. Russian) is
  desirable or required.

Heuristic and sentence-scoped: a requirement only blocks when a proficiency cue
("fluent", "native", "C1"…) sits in the same sentence as the language and the
sentence is not flagged optional ("a plus", "nice to have", "desirable"…).
"""
from __future__ import annotations

import re
from typing import Dict, List

# Canonical language -> word-boundary regex of its names (incl. endonyms).
_TERMS: Dict[str, re.Pattern] = {
    # endonyms + German-style compounds (Deutschkenntnisse, Englischsprachig…); \w*
    # tails catch the compound, with lookaheads to avoid the country name (Deutschland).
    "english": re.compile(r"\benglish\b|\benglisch\w*", re.I),
    "russian": re.compile(r"\brussian\b|\bрусск|\brussisch\w*", re.I),
    "german": re.compile(r"\bgerman\b|\bdeutsch(?!land)\w*", re.I),
    "dutch": re.compile(r"\bdutch\b|\bnederlands\w*|\bniederländisch\w*", re.I),
    "french": re.compile(r"\bfrench\b|\bfran[çc]ais\w*|\bfranzösisch\w*", re.I),
    "spanish": re.compile(r"\bspanish\b|\bespa[ñn]ol\w*|\bcastellano|\bspanisch\w*", re.I),
    "italian": re.compile(r"\bitalian\b|\bitaliano\w*|\bitalienisch\w*", re.I),
    "portuguese": re.compile(r"\bportuguese\b|\bportugu[êe]s\w*", re.I),
    "polish": re.compile(r"\bpolish\b|\bpolski\w*|\bpolnisch\w*", re.I),
    "swedish": re.compile(r"\bswedish\b|\bsvenska\b", re.I),
    "danish": re.compile(r"\bdanish\b", re.I),
    "norwegian": re.compile(r"\bnorwegian\b", re.I),
    "finnish": re.compile(r"\bfinnish\b", re.I),
    "mandarin": re.compile(r"\bmandarin\b|\bchinese\b", re.I),
    "japanese": re.compile(r"\bjapanese\b", re.I),
    "korean": re.compile(r"\bkorean\b", re.I),
    "arabic": re.compile(r"\barabic\b", re.I),
    "hebrew": re.compile(r"\bhebrew\b", re.I),
    "turkish": re.compile(r"\bturkish\b", re.I),
    "ukrainian": re.compile(r"\bukrainian\b", re.I),
    "czech": re.compile(r"\bczech\b", re.I),
    "hungarian": re.compile(r"\bhungarian\b", re.I),
    "greek": re.compile(r"\bgreek\b", re.I),
    "romanian": re.compile(r"\bromanian\b", re.I),
}

_REQ_CUE = re.compile(
    r"\b(native|mother[\s-]?tongue|fluent|fluency|fluently|c1|c2|"
    r"proficien\w*|native[\s-]?level|native speaker|business[\s-]?fluent|"
    r"fließend\w*|fliessend\w*|verhandlungssicher\w*|muttersprach\w*)\b", re.I)

_OPT_CUE = re.compile(
    r"(a plus|plus point|nice[\s-]to[\s-]have|desirable|preferred|preferable|"
    r"bonus|advantageous|is an advantage|ideally|an asset|beneficial|"
    r"would be (a plus|great|nice|beneficial))", re.I)


def _langs_in(segment: str) -> set:
    return {canon for canon, rx in _TERMS.items() if rx.search(segment)}


def assess(text: str, spoken: List[str], boost: List[str]) -> dict:
    spoken_set = {s.lower() for s in spoken}
    boost_set = {b.lower() for b in boost}
    blocking: set = set()
    boost_match = False
    boost_lang = None
    required_boost = False

    for seg in re.split(r"(?<=[.!?])\s+|[\n\r]+", text or ""):
        if not seg.strip():
            continue
        langs = _langs_in(seg)
        if not langs:
            continue
        has_req = bool(_REQ_CUE.search(seg))
        is_opt = bool(_OPT_CUE.search(seg))

        hit = langs & boost_set
        if hit:
            boost_match = True
            boost_lang = boost_lang or sorted(hit)[0]
            if has_req and not is_opt:
                required_boost = True

        if has_req and not is_opt:
            nonspoken = langs - spoken_set
            spoken_here = langs & spoken_set
            # a spoken language only SATISFIES the requirement when it's an explicit
            # alternative ("English or German" / "Englisch oder Deutsch"); "and"/"und"
            # or a comma means both are required, so a non-spoken one still blocks.
            alt = bool(re.search(r"\b(or|oder)\b|/", seg, re.I))
            if nonspoken and not (spoken_here and alt):
                blocking |= nonspoken

    blocking -= spoken_set                       # never block on a spoken language
    return {
        "blocked": bool(blocking),
        "blocking": sorted(blocking),
        "boost_match": boost_match,
        "boost_lang": boost_lang,
        "required_boost": required_boost,
    }
