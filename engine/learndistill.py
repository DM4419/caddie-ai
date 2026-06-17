"""Rebuild the distilled learning layers from the raw style.md log.

- style-examples.md : accepted before/after edits grouped per application (so the
  drafter can sample a balanced set, not 80% one company).
- style-rules.md    : a compact, de-duplicated, generalised do/don't rule set,
  distilled by the model from every captured edit + reason.
The raw style.md is never modified.
"""
from __future__ import annotations

import os

from . import store


def regroup_examples() -> int:
    """Group raw style.md entries by application into style-examples.md. Returns count."""
    blocks = store._style_blocks(store.read_style())
    by_co = {}
    for company, block in blocks:
        by_co.setdefault(company, []).append(block)
    out = ["# Accepted edits, by application",
           "", "Before/after edits the user accepted and applied, grouped per role. "
           "The drafter samples a balanced set across applications.", ""]
    for company in sorted(by_co):
        out.append(f"\n# === {company} ({len(by_co[company])}) ===\n")
        out.extend(by_co[company])
    store.write_style_examples("\n".join(out))
    return len(blocks)


DISTILL_SYSTEM = (
    "You distil a candidate's manual CV/cover-letter edits into a COMPACT, DE-DUPLICATED "
    "rule set for an AI that drafts his applications. Input: many 'AI suggested / Changed to / "
    "Reason' entries across several companies. Output: a tight list of GENERALISED do/don't rules "
    "in his own blunt voice — merge duplicates (e.g. the em-dash rule should appear ONCE), drop "
    "anything company-specific, keep only what generalises. Group under headings: VOICE & TONE, "
    "HARD DON'TS, CV, COVER LETTER. Aim for 12-20 crisp bullets total. Output only the markdown rules.")


def distill_rules() -> bool:
    raw = store.read_style().strip()
    if not raw:
        return False
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return False
    try:
        import anthropic
    except ImportError:
        return False
    from .draft import DEFAULT_MODEL
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=DEFAULT_MODEL, max_tokens=2000, system=DISTILL_SYSTEM,
            messages=[{"role": "user", "content": "Distil these into the rule set:\n\n" + raw[:60000]}])
        rules = "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception:
        return False
    if not rules:
        return False
    store.write_style_rules("# Distilled drafting rules (auto-generated from style.md — hand-edit freely)\n\n"
                            + rules + "\n")
    return True


def rebuild() -> dict:
    n = regroup_examples()
    ok = distill_rules()
    return {"examples_entries": n, "rules_distilled": ok}


def rebuild_if_stale() -> dict | None:
    """Re-distil the rule set ONLY when the raw style.md log has changed since the
    rules were last built. Called right before each draft generation so it always
    reflects the latest accepted edits — with no schedule (which could draft from
    an obsolete rule set) and no wasted LLM call when nothing has changed.
    Returns the rebuild summary if it ran, else None."""
    raw_p = store.STYLE_PATH
    rules_p = store.STYLE_RULES_PATH
    if not raw_p.exists() or not store.read_style().strip():
        return None
    if rules_p.exists() and rules_p.stat().st_mtime >= raw_p.stat().st_mtime:
        return None                      # rules already at least as fresh as the log
    return rebuild()
