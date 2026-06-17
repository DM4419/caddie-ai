"""Bulk 'paste a revised cover letter -> infer why each change was made'.

The inverse of the line-by-line learning loop: the user pastes their fully
revised letter, we diff it against the current draft (paragraph level), and ask
the model to infer a concise reason for each changed passage — grounded in the
user's existing learned preferences (style.md). The user reviews/edits the
reasons; on save they are written to style.md and the draft is updated.
"""
from __future__ import annotations

import difflib
import html as _html
import os
import re

from bs4 import BeautifulSoup

from . import draft, store


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    blocks = soup.find_all(["p", "li", "h1", "h2", "h3", "div"])
    if blocks:
        parts = [b.get_text().strip() for b in blocks if b.get_text().strip()]
        return "\n\n".join(parts)
    return soup.get_text().strip()


def text_to_html(text: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "\n".join("<p>" + _html.escape(p).replace("\n", "<br>") + "</p>" for p in paras)


def segment_changes(old_text: str, new_text: str) -> list:
    """Paragraph-level diff -> list of (old_segment, new_segment) for changed blocks."""
    old_ps = [p.strip() for p in re.split(r"\n\s*\n", old_text or "") if p.strip()]
    new_ps = [p.strip() for p in re.split(r"\n\s*\n", new_text or "") if p.strip()]
    sm = difflib.SequenceMatcher(a=old_ps, b=new_ps, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old_seg = "\n\n".join(old_ps[i1:i2])
        new_seg = "\n\n".join(new_ps[j1:j2])
        if old_seg.strip() == new_seg.strip():
            continue
        out.append((old_seg, new_seg))
    return out


def infer_reasons(changes: list, role: str, company: str) -> list:
    """One model call: infer a short reusable reason per (old,new) change.
    Falls back to a neutral description if no API key / on error."""
    if not changes:
        return []
    fallback = ["Reworded by hand — add your reason." for _ in changes]
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return fallback
    try:
        import anthropic
    except ImportError:
        return fallback
    learn = store.read_style_recent(16000)
    pairs = "\n\n".join(
        f"@@@{i+1}@@@\nOLD: {o or '(nothing — newly added)'}\nNEW: {n or '(deleted)'}"
        for i, (o, n) in enumerate(changes))
    system = (
        "You infer WHY the candidate changed each passage of his cover letter, phrased as a short, "
        "reusable writing preference in his own blunt voice (one sentence, like his existing reasons). "
        "Match the tone and specificity of his past reasons below. Never invent a motive you cannot see "
        "in the change; if it is just rewording, say what improved (tighter, less AI-sounding, removed an "
        "invented claim, dropped an em-dash, more grounded, etc.). Return EXACTLY one line per change as "
        "'@@@N@@@ <reason>'.\n\nHis past reasons / learned rules:\n" + learn)
    user = (f"Job: {role} at {company}\n\nCHANGES (old vs his new version):\n{pairs}\n\n"
            f"Give one reason line per change, numbered to match.")
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(model=draft.DEFAULT_MODEL, max_tokens=1200,
                                     system=system, messages=[{"role": "user", "content": user}])
        raw = "".join(b.text for b in msg.content if b.type == "text")
    except Exception:
        return fallback
    reasons = list(fallback)
    for m in re.finditer(r"@@@(\d+)@@@\s*(.*?)(?=@@@\d+@@@|$)", raw, re.DOTALL):
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(reasons):
            reasons[idx] = m.group(2).strip()
    return reasons
