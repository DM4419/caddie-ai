"""Board/ATS-specific optimisation for the application package.

Detects the ATS a job came from (Greenhouse / Lever / Ashby) and supplies:
  - drafting_directives(url): guidance injected into the draft LLM prompt so the
    CV, cover letter and screening answers are tuned to how that ATS screens.
  - ui_tip(url): a "how this board screens + what to focus on" panel for the
    review UI.

Mechanics are board-specific; the UNIVERSAL rules apply to every application.
"""
from __future__ import annotations

from typing import Optional

from adapters.ats import parse_careers_url

# Applies to every application, whatever the board.
UNIVERSAL = [
    "Pass knockout/screening questions truthfully (years, work authorisation, "
    "timezone, comp) — they override resume quality.",
    "Give one evidence bullet per stated requirement (aim 80%+ coverage of the "
    "must-haves — strong coverage correlates with ~3.2x interview rate).",
    "Mirror the posting's exact nouns and verbs; spell out AND abbreviate key terms.",
    "Quantify and top-load: 5+ metrics, strongest in the top half of the CV.",
    "Export as PDF and check the parse preview.",
]

PROFILES = {
    "greenhouse": {
        "name": "Greenhouse",
        "mechanics": ("No algorithmic ranking — a human rates you against a scorecard "
                      "(Definitely Not → Strong Yes) built from the job requirements. "
                      "Knockout questions can auto-filter. Recruiters keyword-search the "
                      "database; the resume scan is ~30–45 seconds."),
        "screener_sees": ("Your parsed profile fields + original file, a requirement-derived "
                          "checklist, and your knockout/screening answers."),
        "focus": [
            "Map one concrete, quantified bullet to each requirement on the scorecard.",
            "Top-load the strongest metrics — the scan is 30–45 seconds.",
            "Answer knockout questions truthfully; they can auto-reject before a human looks.",
        ],
        "directives": (
            "TARGET ATS = GREENHOUSE. A human rates the candidate against a scorecard built "
            "from the JD's requirements (Definitely Not / No / Yes / Strong Yes) after a "
            "~30–45s scan. Give ONE concrete, quantified evidence bullet per stated "
            "requirement so each scorecard line is easy to mark 'Strong Yes'; mirror the "
            "JD's exact requirement wording; top-load the strongest metrics in the first "
            "half. Answer any knockout questions directly and truthfully."),
    },
    "lever": {
        "name": "Lever",
        "mechanics": ("Recruiters filter via keyword search that STEMS words but does NOT "
                      "expand acronyms — so write both the acronym and the full term. Scores "
                      "are visible to recruiters. Newer AI 'Talent Fit' ranks applicants; VONQ "
                      "screening (Spring 2026) screens at the point of application."),
        "screener_sees": ("Your parsed profile + file, recruiter keyword filters, and your "
                          "screening answers."),
        "focus": [
            "Write every key term BOTH ways: 'Search Engine Optimization (SEO)'.",
            "Cover the posting's keywords densely — search filters you in or out.",
            "Answer screening questions truthfully; newer AI may gate at application.",
        ],
        "directives": (
            "TARGET ATS = LEVER. Recruiters filter via keyword search that stems words but "
            "does NOT expand acronyms. Write every important term in BOTH forms — spelled-out "
            "and abbreviated (e.g. 'Search Engine Optimization (SEO)') — at least once; cover "
            "the posting's exact keywords densely and naturally; mirror its nouns and verbs. "
            "Keep screening answers truthful and direct."),
    },
    "ashby": {
        "name": "Ashby",
        "mechanics": ("AI checks your resume against recruiter-defined criteria and returns a "
                      "fit level WITH citations — it deliberately does not rank or score "
                      "numerically; a human decides. Hiring managers score 1–4 (3+ = pass). "
                      "PII is redacted before the AI sees it."),
        "screener_sees": ("Your parsed profile + file, an AI fit level with citations against "
                          "recruiter criteria, and your screening answers."),
        "focus": [
            "Make each requirement explicitly citable — criterion wording next to the evidence.",
            "One clear evidence bullet per criterion so the AI can cite it.",
            "Aim each hiring-manager-scored area at a 3+ : concrete, unambiguous proof.",
        ],
        "directives": (
            "TARGET ATS = ASHBY. An AI matches the resume against recruiter-defined criteria "
            "and returns a fit level WITH CITATIONS (no numeric ranking); a human then scores "
            "1–4 (3+ passes). Optimise for citability: for each JD criterion, place wording "
            "that mirrors that criterion immediately beside concrete, quantified evidence so "
            "the AI can cite a clear match. One unambiguous evidence bullet per criterion. "
            "Keep screening answers truthful and specific."),
    },
}


def detect(url: str) -> str:
    """Return 'greenhouse' | 'lever' | 'ashby' for a job URL, else ''."""
    info = parse_careers_url(url) if url else None
    prov = (info or {}).get("provider", "")
    return prov if prov in PROFILES else ""


def drafting_directives(url: str) -> str:
    """Board-specific + universal guidance for the draft LLM prompt."""
    universal = "Universal best practice: " + " ".join(UNIVERSAL)
    prov = detect(url)
    return (PROFILES[prov]["directives"] + "\n" + universal) if prov else universal


def ui_tip(url: str) -> Optional[dict]:
    """Panel content for the review UI. Always returns a tip (generic if the board
    isn't a recognised ATS) so every application gets the focus checklist."""
    prov = detect(url)
    if prov:
        p = PROFILES[prov]
        return {"board": p["name"], "specific": True, "mechanics": p["mechanics"],
                "screener_sees": p["screener_sees"], "focus": p["focus"]}
    return {"board": "General ATS", "specific": False,
            "mechanics": ("This posting isn't on a recognised ATS (Greenhouse / Lever / "
                          "Ashby), so apply the universal rules below."),
            "screener_sees": "Typically your parsed profile + file, plus any screening answers.",
            "focus": UNIVERSAL}
