"""Normalized data shapes shared across the engine.

One `Job` shape across all sources (Phase 0 has only paste/URL).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class FactorScore(BaseModel):
    key: str            # remote | skills | domain | stage
    label: str
    weight: int         # max points for this factor (from profile weights)
    points: int         # awarded points, 0..weight
    detail: str         # one-line explanation


class ScoreResult(BaseModel):
    score: int                      # 0..100
    factors: List[FactorScore]
    reason: str                     # one-line "why N"
    drivers: List[str] = Field(default_factory=list)  # verbatim JD quotes
    unmet: List[str] = Field(default_factory=list)    # requirement tags not met
    language_block: bool = False    # requires a language the user doesn't speak
    language_note: str = ""         # one-line language explanation, if any


class Job(BaseModel):
    id: str
    date: str                       # YYYY-MM-DD added
    role: str
    company: str
    url: str = ""
    mode: str = "remote"            # remote | hybrid | onsite
    location: str = ""              # free-text location, if known
    salary: str = ""                # formatted salary range, if known
    posted: str = ""                # posting date YYYY-MM-DD, if known
    description: str = ""
    status: str = "new"             # new | review | approved | applied | skipped
    bookmarked: bool = False        # user-pinned; never auto-archived
    archived: bool = False          # aged out past the recency window
    score: int = 0                  # primary AI fit score (LLM); keyword fallback
    weight_score: int = 0           # secondary: weighted keyword factors total
    reason: str = ""
    factors: List[FactorScore] = Field(default_factory=list)
    drivers: List[str] = Field(default_factory=list)
    unmet: List[str] = Field(default_factory=list)
    language_block: bool = False
    language_note: str = ""
    role_off_target: bool = False
    role_note: str = ""
    source: str = ""                # board this job came from (e.g. "Reed")
    remote_anywhere: bool = False   # location-independent / work-from-anywhere
    flags: List[str] = Field(default_factory=list)  # founder-fit: eir|zero_to_one|founder_welcome
    live: str = ""                  # "" unknown | live | expired | error
    live_note: str = ""             # one-line liveness explanation
    live_checked_at: str = ""       # ISO timestamp of the last liveness check
    analysis: Optional["Analysis"] = None
    jd: Optional["JDDoc"] = None
    draft: Optional["Draft"] = None


class Requirement(BaseModel):
    quote: str = ""                 # verbatim phrase from the JD
    level: str = "stretch"          # match | stretch | mismatch
    note: str = ""


class JDDoc(BaseModel):
    text: str = ""                  # the fetched job description
    requirements: List[Requirement] = Field(default_factory=list)
    url: str = ""
    fetched_at: str = ""
    error: str = ""


class FitDimension(BaseModel):
    label: str = ""
    score: int = 0                  # 0..100, semantic (CV-based, not keywords)
    note: str = ""


class Analysis(BaseModel):
    score_rationale: str = ""       # why this specific score — what lifted/held it
    best_fit: str = ""
    shortcomings: str = ""
    breakdown: List[FitDimension] = Field(default_factory=list)
    skills_matched: List[str] = Field(default_factory=list)
    skills_all: List[str] = Field(default_factory=list)
    unmet: List[str] = Field(default_factory=list)
    generated_at: str = ""
    error: str = ""


class Draft(BaseModel):
    cv_html: str = ""
    cl_html: str = ""
    screening_html: str = ""
    generated_at: str = ""
    model: str = ""
    error: str = ""                 # set when generation failed / no API key
    cv_used: Optional[dict] = None  # {id,name} of the application-CV variant drafted from


Job.model_rebuild()
