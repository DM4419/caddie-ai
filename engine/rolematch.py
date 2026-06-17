"""Decide whether a job is a target role (PM-family) vs off-target noise.

Checked against the job TITLE (reliable) — not the whole JD, which would
false-match on any passing mention of "product manager". Exclude wins over
include so "Head of Product Marketing" (matches "head of product" but is a
marketing role) is correctly off-target.
"""
from __future__ import annotations

import re
from typing import List, Optional


def _first_match(text: str, terms: List[str]) -> Optional[str]:
    t = (text or "").lower()
    for term in terms or []:
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", t):
            return term
    return None


def assess(role_text: str, include: List[str], exclude: List[str]) -> dict:
    """Return {match: bool, note: str} for a title/role string."""
    ex = _first_match(role_text, exclude)
    if ex:
        return {"match": False, "note": f"{ex.title()} — off-target role"}
    inc = _first_match(role_text, include)
    if inc:
        return {"match": True, "note": ""}
    return {"match": False, "note": "not a target PM role"}
