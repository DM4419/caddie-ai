"""Score a JD 0-100 using the weights in profile.yaml (never hardcoded).

Phase 0 scoring is a deterministic keyword heuristic — cheap, no LLM. It returns
a per-factor breakdown and a one-line reason for the review UI.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from . import language
from .models import FactorScore, ScoreResult


def _count_hits(text: str, terms: List[str]) -> Tuple[int, List[str]]:
    """Count distinct terms present as WHOLE words/phrases (not substrings).

    Whole-word matching means short terms like "ai" match "AI" but not "email"
    or "training", and "data" doesn't match "database".
    """
    t = text.lower()
    hits = [term for term in terms
            if re.search(r"\b" + re.escape(term.lower()) + r"\b", t)]
    return len(hits), hits


def _remote_factor(mode: str, weight: int) -> FactorScore:
    ratio, detail = {
        "remote": (1.0, "full remote"),
        "hybrid": (0.6, "hybrid (partial)"),
        "onsite": (0.1, "onsite"),
    }.get(mode, (0.6, mode))
    return FactorScore(
        key="remote", label="Full remote", weight=weight,
        points=round(weight * ratio), detail=detail,
    )


def _ratio_factor(key: str, label: str, weight: int, text: str,
                  terms: List[str], full_at: int) -> FactorScore:
    """full_at = number of distinct keyword hits that earns full points."""
    n, hits = _count_hits(text, terms)
    ratio = min(1.0, n / full_at) if full_at else 0.0
    sample = ", ".join(hits[:3]) if hits else "none found"
    return FactorScore(
        key=key, label=label, weight=weight,
        points=round(weight * ratio), detail=f"{n} match(es): {sample}",
    )


def score_job(job_dict: dict, profile: dict) -> ScoreResult:
    """job_dict needs `mode` and `description`. Weights come from profile."""
    w = profile["weights"]
    text = job_dict.get("description", "") or ""
    mode = job_dict.get("mode", "remote")

    # Factors are config-driven: a factor only counts if its weight key is present
    # in profile.yaml. (Remote was removed — location is a hard gate, not a score.)
    factors = []
    if "remote" in w:
        factors.append(_remote_factor(mode, int(w["remote"])))
    if "skills" in w:
        factors.append(_ratio_factor("skills", "Skills & quals", int(w["skills"]), text,
                                     profile.get("skills", []), full_at=4))
    if "domain" in w:
        factors.append(_ratio_factor("domain", "Domain match", int(w["domain"]), text,
                                     profile.get("domains", []), full_at=2))
    if "stage" in w:
        factors.append(_ratio_factor("stage", "Stage / operating style", int(w["stage"]), text,
                                     profile.get("stage_signals", []), full_at=1))
    score = sum(f.points for f in factors)

    # Reason = the strongest factors, in plain words.
    ranked = sorted(factors, key=lambda f: f.points / max(f.weight, 1), reverse=True)
    strong = [f.label.lower() for f in ranked if f.weight and f.points / f.weight >= 0.6]
    reason = " + ".join(strong[:3]) if strong else "weak match across factors"

    # ---- spoken-language gate + boost ----
    lcfg = profile.get("languages") or {}
    la = language.assess(text, lcfg.get("spoken", ["english"]), lcfg.get("boost", []))
    note = ""
    if la["boost_match"]:
        pts = int(lcfg.get("boost_points", 12))
        if not la["required_boost"]:
            pts = round(pts / 2)
        score = min(100, score + pts)
        lvl = "required" if la["required_boost"] else "desirable"
        bl = (la["boost_lang"] or "russian").title()
        note = f"{bl} {lvl} (+{pts})"
        reason = f"{reason} · {bl} a plus"
    if la["blocked"]:
        cap = int(lcfg.get("block_score_cap", 20))
        score = min(score, cap)
        langs = ", ".join(s.title() for s in la["blocking"])
        note = f"Requires fluent {langs} — not spoken"
        reason = note   # the blocker is the headline

    return ScoreResult(score=score, factors=factors, reason=reason,
                       language_block=la["blocked"], language_note=note)
