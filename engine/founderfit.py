"""Detect 'founder-fit' signals in a job: roles where ex-founders are welcome,
roles centred on 0→1 building, and Entrepreneur/Founder-in-Residence positions.
Keyword-based (fast, no LLM) so it runs at import time and backfills cheaply.

Returns a list of flags from: "eir", "zero_to_one", "founder_welcome".
"""
from __future__ import annotations

import re

EIR_RE = re.compile(
    r"\b(entrepreneur|founder|ceo)[\s-]+in[\s-]+residence\b|\beir\b|\bxir\b", re.I)

ZERO_TO_ONE_RE = re.compile(
    r"0\s*[-–—>to]+\s*1\b|\bzero[\s-]to[\s-]one\b|\bgreenfield\b|"
    r"\bfrom\s+scratch\b|\bground[\s-]up\b|\bblank\s+(page|canvas)\b|"
    r"\bfirst\s+product\b|\b0\s*→\s*1\b|\bnew\s+0\b|build(ing)?\s+from\s+(zero|the\s+ground)", re.I)

FOUNDER_WELCOME_RE = re.compile(
    r"ex[\s-]?founder|former\s+founder|previously\s+founded|founded\s+(a|your|their|own|companies?)|"
    r"serial\s+entrepreneur|entrepreneurial\s+(background|mindset|experience|spirit)|"
    r"founder[\s-]friendly|founder['’]?s?\s+(mindset|mentality|dna)|"
    r"started\s+(a|your\s+own|companies?)|\bex[\s-]?operator\b|"
    r"if\s+you[’'a-z\s]{0,24}founded", re.I)

FLAG_LABEL = {
    "eir": "EIR / Founder-in-Residence",
    "zero_to_one": "0→1 build",
    "founder_welcome": "Ex-founders welcome",
}


def detect(title: str = "", description: str = "") -> list:
    """Return the founder-fit flags present in this job."""
    title = title or ""
    body = f"{title}\n{description or ''}"
    flags = []
    # EIR is usually in the title, but accept the body too
    if EIR_RE.search(title) or EIR_RE.search(body):
        flags.append("eir")
    if ZERO_TO_ONE_RE.search(body):
        flags.append("zero_to_one")
    if FOUNDER_WELCOME_RE.search(body):
        flags.append("founder_welcome")
    return flags
