"""Deterministic CVData -> clean one-page HTML. No LLM here — layout only.

Two modes:
- editable=True (live preview): missing facts (name, contact, school/employer
  names, dates) render as click-to-edit YELLOW placeholders, and filled values
  render as click-to-edit spans. Lets people type names/dates straight onto the
  CV instead of dictating them into the chat.
- editable=False (print/PDF): plain text; empty fields are simply omitted.

Editable spans carry data-sec / data-i / data-field so the UI can PATCH one
field back to CVData via /api/cvbuilder/{id}/field.
"""
from __future__ import annotations

from html import escape
from typing import List

from .models import CVData, Entry, SECTION_TITLES

# Default order when the goal hasn't set one: Profile > Skills > Work Experience >
# Projects > Education > Interests (volunteering/achievements slot in before
# interests). Empty sections aren't rendered, so a student with no jobs still reads
# well; the goal inference can override this order (e.g. education-first for students).
DEFAULT_ORDER = ["summary", "skills", "experience", "projects", "education",
                 "volunteering", "achievements", "interests"]

# Placeholder prompts per (section, field).
_LABELS = {
    ("education", "title"): "qualification (e.g. GCSEs)",
    ("education", "org"): "school / college",
    ("experience", "title"): "job title",
    ("experience", "org"): "employer",
    ("projects", "title"): "project name",
    ("projects", "org"): "where / context",
    ("volunteering", "title"): "role",
    ("volunteering", "org"): "organisation",
}


def _dates(e: Entry) -> str:
    if e.start and e.end:
        return f"{e.start} – {e.end}"
    return e.end or e.start or ""


def _field(value: str, sec: str, i: int, field: str, label: str, editable: bool) -> str:
    """Editable span (or yellow placeholder when empty); plain text in print mode."""
    if not editable:
        return escape(value or "")
    attrs = f'data-sec="{sec}" data-i="{i}" data-field="{field}"'
    if value:
        return f'<span class="cvb-edit" {attrs}>{escape(value)}</span>'
    return f'<span class="cvb-edit cvb-ph" {attrs}>+ {escape(label)}</span>'


def _entry_html(e: Entry, sec: str, i: int, editable: bool) -> str:
    title = _field(e.title, sec, i, "title", _LABELS.get((sec, "title"), "title"), editable)
    org = _field(e.org, sec, i, "org", _LABELS.get((sec, "org"), "organisation"), editable)
    dates = _field(_dates(e), sec, i, "dates", "years", editable)
    loc = escape(e.location) if e.location else ""
    org_line = org + (f" · {loc}" if loc else "")
    bullets = "".join(f"<li>{escape(b)}</li>" for b in e.bullets if b.strip())
    show_org = editable or (e.org or e.location)
    show_dates = editable or _dates(e)
    return (
        '<div class="cvb-entry">'
        f'<div class="cvb-entry-head"><span class="cvb-entry-title">{title}</span>'
        + (f'<span class="cvb-entry-dates">{dates}</span>' if show_dates else "")
        + "</div>"
        + (f'<div class="cvb-entry-org">{org_line}</div>' if show_org else "")
        + (f'<div class="cvb-entry-sum">{escape(e.summary)}</div>' if e.summary else "")
        + (f"<ul>{bullets}</ul>" if bullets else "")
        + "</div>"
    )


def _section(title: str, inner: str) -> str:
    if not inner.strip():
        return ""
    return f'<section class="cvb-section"><h2>{escape(title)}</h2>{inner}</section>'


def _entries_section(key: str, entries: List[Entry], editable: bool) -> str:
    if not entries:
        return ""
    inner = "".join(_entry_html(e, key, i, editable) for i, e in enumerate(entries))
    return _section(SECTION_TITLES.get(key, key.title()), inner)


def _tags_section(key: str, items: List[str]) -> str:
    chips = "".join(f'<span class="cvb-chip">{escape(i)}</span>'
                    for i in items if i and i.strip())
    return _section(SECTION_TITLES.get(key, key.title()),
                    f'<div class="cvb-chips">{chips}</div>' if chips else "")


def render_html(cv: CVData, editable: bool = True) -> str:
    order = list(cv.section_order or DEFAULT_ORDER)
    for k in DEFAULT_ORDER:                       # append any sections the order missed
        if k not in order:
            order.append(k)

    blocks = []
    for key in order:
        if key == "summary":
            inner = _field(cv.summary, "", -1, "summary", "a short profile line", editable) \
                if (editable or cv.summary) else ""
            blocks.append(_section(SECTION_TITLES["summary"], f"<p>{inner}</p>" if inner else ""))
        elif key in ("experience", "education", "projects", "volunteering"):
            blocks.append(_entries_section(key, getattr(cv, key), editable))
        elif key == "skills":
            blocks.append(_tags_section(key, cv.skills))
        elif key == "interests":
            blocks.append(_tags_section(key, cv.interests))
        elif key == "achievements":
            inner = "".join(f"<li>{escape(a)}</li>" for a in cv.achievements if a.strip())
            blocks.append(_section(SECTION_TITLES["achievements"],
                                   f"<ul>{inner}</ul>" if inner else ""))

    # Header — name, headline, contact: all click-to-edit in preview.
    name = _field(cv.name, "", -1, "name", "your name", editable) or "Your name"
    headline = (f'<div class="cvb-headline">'
                f'{_field(cv.headline, "", -1, "headline", "headline for your goal", editable)}</div>'
                if (editable or cv.headline) else "")
    if editable:
        contact = " · ".join([
            _field(cv.email, "", -1, "email", "email", editable),
            _field(cv.phone, "", -1, "phone", "phone", editable),
            _field(cv.location, "", -1, "location", "location", editable),
            *[escape(l) for l in cv.links],
        ])
    else:
        contact = " · ".join(escape(x) for x in [cv.email, cv.phone, cv.location, *cv.links] if x)
    header = (
        '<header class="cvb-header">'
        f"<h1>{name}</h1>{headline}"
        + (f'<div class="cvb-contact">{contact}</div>' if contact else "")
        + "</header>"
    )
    return f'<article class="cvb-cv">{header}{"".join(b for b in blocks if b)}</article>'
