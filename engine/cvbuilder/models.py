"""Pydantic models for the conversational CV builder.

CVData is the deterministic-render target — it must work for a GCSE leaver
(education / projects / volunteering / interests) AND a senior professional
(experience-first). `section_order` lets the goal drive the layout.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

# Canonical section keys. The goal decides which appear and in what order.
SECTION_KEYS = ["summary", "experience", "education", "projects",
                "volunteering", "achievements", "skills", "interests"]

SECTION_TITLES = {
    "summary": "Profile",
    "experience": "Work experience",
    "education": "Education",
    "projects": "Projects",
    "volunteering": "Volunteering & activities",
    "achievements": "Achievements",
    "skills": "Skills",
    "interests": "Interests",
}


class Entry(BaseModel):
    """One role / qualification / project / activity."""
    title: str = ""                 # job title, qualification, or project name
    org: str = ""                   # employer, school, club
    location: str = ""
    start: str = ""                 # free-text date, e.g. "2023" or "Jun 2024"
    end: str = ""                   # "" or "Present"
    summary: str = ""               # optional one-liner of context
    bullets: List[str] = Field(default_factory=list)   # achievements, ideally quantified


class CVData(BaseModel):
    name: str = ""
    headline: str = ""              # goal-aligned one-liner under the name
    email: str = ""
    phone: str = ""
    location: str = ""
    links: List[str] = Field(default_factory=list)     # LinkedIn / portfolio / GitHub
    summary: str = ""               # short profile paragraph
    experience: List[Entry] = Field(default_factory=list)
    education: List[Entry] = Field(default_factory=list)
    projects: List[Entry] = Field(default_factory=list)
    volunteering: List[Entry] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    interests: List[str] = Field(default_factory=list)
    # Goal-driven layout order; empty -> render.py picks a sensible default.
    section_order: List[str] = Field(default_factory=list)


class GoalSpec(BaseModel):
    """What the person is aiming for — drives structure, emphasis, and skills."""
    raw: str = ""                   # the user's own free-text goal, verbatim
    sector: str = ""                # e.g. "Healthcare", "Software", "Retail"
    role_type: str = ""             # e.g. "Apprenticeship", "Product Manager"
    seniority: str = "entry"        # student | entry | mid | senior | exec
    target_summary: str = ""        # one line: the ideal role this CV aims at
    emphasis: List[str] = Field(default_factory=list)          # what to foreground
    suggested_section_order: List[str] = Field(default_factory=list)
    suggested_skills: List[str] = Field(default_factory=list)   # labels to offer


class AssessScore(BaseModel):
    key: str                        # ats | domain | results | bio
    label: str                      # display label
    score: int = 0                  # 0-100
    note: str = ""                  # short why (<= ~6 words)


class Assessment(BaseModel):
    """Scorecard for an imported CV, shown above the builder."""
    summary: str = ""               # 1 sentence: overall impression
    recommendation: str = ""        # 1 sentence: the single best improvement
    scores: List[AssessScore] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: str                       # "assistant" | "user"
    text: str


class BuilderSession(BaseModel):
    id: str
    created: str                    # ISO timestamp
    # intro -> goal -> interview -> skills -> review
    step: str = "intro"
    goal: Optional[GoalSpec] = None
    imported_text: str = ""         # raw text of an uploaded/pasted CV, if any
    assessment: Optional[Assessment] = None   # scorecard for the imported CV
    cv: CVData = Field(default_factory=CVData)
    messages: List[ChatMessage] = Field(default_factory=list)
