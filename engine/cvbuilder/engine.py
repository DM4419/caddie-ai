"""LLM logic for the CV builder: goal inference, the adaptive interviewer,
structured extraction, skill suggestions, and import assessment.

Design rule: the LLM produces DATA or chat turns — never the final layout.
All structured calls return validated CVData / GoalSpec; render.py does layout.
Degrades gracefully (scripted fallbacks) when ANTHROPIC_API_KEY is absent.
"""
from __future__ import annotations

import json
import os
from typing import List, Tuple

from .models import (CVData, GoalSpec, SECTION_KEYS, BuilderSession)

INTERVIEW_MODEL = os.environ.get("CVBUILDER_MODEL", "claude-sonnet-4-6")
EXTRACT_MODEL = os.environ.get("CVBUILDER_EXTRACT_MODEL", "claude-haiku-4-5")


# ---- low-level Anthropic helpers -----------------------------------------
def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None


def _complete(system: str, user: str, max_tokens: int = 800,
              model: str = INTERVIEW_MODEL) -> str:
    client = _client()
    if client is None:
        return ""
    msg = client.messages.create(
        model=model, max_tokens=max_tokens,
        system=[{"type": "text", "text": system}],
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def _complete_json(system: str, user: str, max_tokens: int = 2000,
                   model: str = EXTRACT_MODEL) -> dict:
    raw = _complete(system, user, max_tokens=max_tokens, model=model)
    if not raw:
        return {}
    s, e = raw.find("{"), raw.rfind("}")
    try:
        return json.loads(raw[s:e + 1] if s != -1 else raw)
    except Exception:
        return {}


# ---- goal: free text -> structured spec ----------------------------------
GOAL_SYSTEM = """You turn a job-seeker's free-text GOAL into a structured plan
for their CV. They may be a school leaver, a career changer, or a professional —
infer, don't assume seniority. Return ONLY JSON:
{"sector":"<industry, e.g. Healthcare/Retail/Software>",
 "role_type":"<the kind of role, e.g. 'Apprenticeship', 'Retail assistant', 'Product Manager'>",
 "seniority":"student|entry|mid|senior|exec",
 "target_summary":"<one plain sentence: the ideal role this CV should aim at>",
 "emphasis":["<what to foreground for this goal>", ...up to 4],
 "suggested_section_order":["<ordered subset of: summary, experience, education, projects, volunteering, achievements, skills, interests>"],
 "suggested_skills":["<8-14 skill labels relevant to this goal AND realistic for their level — include transferable/soft skills for early-career>"]}
For students / little work history, put education and projects/volunteering BEFORE
experience. For professionals, experience first. Keep skill labels short (1-3 words)."""


def infer_goal(raw_goal: str) -> GoalSpec:
    data = _complete_json(GOAL_SYSTEM, f"GOAL: {raw_goal}\n\nReturn the JSON.",
                          model=EXTRACT_MODEL)
    order = [k for k in (data.get("suggested_section_order") or []) if k in SECTION_KEYS]
    return GoalSpec(
        raw=raw_goal,
        sector=str(data.get("sector", ""))[:60],
        role_type=str(data.get("role_type", ""))[:60],
        seniority=str(data.get("seniority", "entry"))[:10] or "entry",
        target_summary=str(data.get("target_summary", ""))[:200],
        emphasis=[str(x)[:60] for x in (data.get("emphasis") or [])][:4],
        suggested_section_order=order,
        suggested_skills=[str(x)[:40] for x in (data.get("suggested_skills") or [])][:14],
    )


# ---- the interviewer ------------------------------------------------------
def _interview_system(session: BuilderSession) -> str:
    g = session.goal
    goal_line = (f"Their goal: {g.target_summary or g.raw} "
                 f"(sector: {g.sector or '?'}, level: {g.seniority})." if g else
                 "Their goal is not captured yet — ask for it first, in plain words.")
    return f"""You are a warm, encouraging CV coach interviewing ONE person to build
their CV. {goal_line}

How to behave:
- Plain, friendly English. NEVER use jargon. Assume they have never written a CV.
- Ask ONE focused question at a time. Briefly acknowledge their last answer first.
- Do NOT assume they have work experience. For students / early-career, draw
  achievements out of school, college, part-time or Saturday jobs, clubs, sports,
  volunteering, hobbies, and personal projects.
- ALWAYS push gently for QUANTIFIABLE results: how many, how much, how often, how
  long, what changed, what was the outcome. If they give a vague answer, ask a
  follow-up that helps them put a number or concrete result on it.
- Do NOT ask for the exact NAMES of schools/employers, the DATES/years, or contact
  details — the person types those straight onto the CV in the highlighted boxes.
  Spend your questions on WHAT they did and the RESULTS, not the admin facts.
- Keep it short — one or two sentences, then the question. Be human, not a form.
- When you genuinely have enough for a solid CV (contact basics, the main
  experiences/education for their goal, a few quantified achievements, and skills),
  say so warmly and tell them they can review their CV. Do not drag it out."""


def interview_turn(session: BuilderSession) -> str:
    """Return the assistant's next chat message given the conversation so far."""
    if _client() is None:
        return _scripted_question(session)
    transcript = "\n".join(f"{m.role.upper()}: {m.text}" for m in session.messages[-16:])
    have = _coverage(session.cv)
    user = (f"Conversation so far:\n{transcript or '(none yet)'}\n\n"
            f"What the CV already has: {have}\n\n"
            "Write your next message to them (acknowledge + one question).")
    try:
        out = _complete(_interview_system(session), user, max_tokens=400,
                        model=INTERVIEW_MODEL)
        return out or _scripted_question(session)
    except Exception:
        return _scripted_question(session)


def _coverage(cv: CVData) -> str:
    bits = []
    if cv.name: bits.append("name")
    if cv.email or cv.phone: bits.append("contact")
    if cv.experience: bits.append(f"{len(cv.experience)} job(s)")
    if cv.education: bits.append(f"{len(cv.education)} education")
    if cv.projects: bits.append(f"{len(cv.projects)} project(s)")
    if cv.volunteering: bits.append(f"{len(cv.volunteering)} activity(ies)")
    if cv.skills: bits.append(f"{len(cv.skills)} skills")
    return ", ".join(bits) or "nothing yet"


def _scripted_question(session: BuilderSession) -> str:
    """No-API fallback so the flow still runs."""
    cv = session.cv
    if not session.goal:
        return ("Hi! I'll help you build a great CV. First — in your own words, "
                "what kind of role or course are you hoping to apply for?")
    if not cv.name:
        return "Great. Let's start simple — what's your full name?"
    if not (cv.email or cv.phone):
        return "Thanks! How can an employer reach you — an email and/or phone number?"
    if not (cv.education or cv.experience):
        return ("Tell me about something you've done — a job, a school or college "
                "course, or a project. What was it, and what did you do?")
    return ("Nice. Can you put a number on that — how many people, how much time "
            "saved, or what the result was?")


# ---- structured extraction: transcript (+import) -> CVData ----------------
EXTRACT_SYSTEM = """You extract a structured CV from a conversation transcript and
any imported CV text. Return ONLY JSON matching this shape (omit unknown fields):
{"name":"","headline":"<short goal-aligned tagline>","email":"","phone":"",
 "location":"","links":[],"summary":"<2-3 COMPLETE sentences, a profile aligned to the goal; finish every sentence and name the target role/sector — never trail off mid-phrase>",
 "experience":[{"title":"","org":"","location":"","start":"","end":"","summary":"","bullets":["<prefer quantified achievements>"]}],
 "education":[{"title":"<qualification>","org":"<school/college>","start":"","end":"","bullets":["grades / highlights"]}],
 "projects":[{"title":"","org":"","bullets":[]}],
 "volunteering":[{"title":"","org":"","bullets":[]}],
 "achievements":["<short line>"],"skills":["<label>"],"interests":["<label>"]}
RULES:
- Use ONLY facts stated by the person or in the imported CV. NEVER invent
  employers, grades, dates, or numbers. Leave blank what you don't know.
- Prefer bullets that include a concrete number or outcome the person gave.
- Merge with what's already captured — do not drop existing data.
- Write the summary and every bullet as COMPLETE sentences — never cut off mid-phrase.
- Keep it concise and honest; this is a real person's CV."""


def extract_cv(session: BuilderSession) -> CVData:
    if _client() is None:
        return session.cv
    transcript = "\n".join(f"{m.role.upper()}: {m.text}" for m in session.messages)
    g = session.goal
    goal_block = (f"GOAL: {g.raw}\nTarget role: {g.target_summary or g.role_type} | "
                  f"sector: {g.sector} | level: {g.seniority}") if g else "GOAL: (not captured)"
    user = (f"{goal_block}\n\n"
            f"IMPORTED CV TEXT (may be empty):\n{(session.imported_text or '')[:6000]}\n\n"
            f"ALREADY CAPTURED (JSON):\n{session.cv.model_dump_json()}\n\n"
            f"CONVERSATION:\n{transcript}\n\nReturn the merged CV JSON.")
    data = _complete_json(EXTRACT_SYSTEM, user, max_tokens=6000, model=EXTRACT_MODEL)
    if not data:
        return session.cv
    try:
        cv = CVData(**{k: v for k, v in data.items() if k in CVData.model_fields})
    except Exception:
        return session.cv
    # Preserve choices the user made directly that the model might not echo back.
    cv.skills = cv.skills or session.cv.skills
    cv.section_order = session.cv.section_order   # layout order is fixed in render.py; goal never reorders
    return cv


# ---- import assessment (scorecard) ----------------------------------------
ASSESS_LABELS = [("ats", "ATS"), ("domain", "Domain"),
                 ("results", "Results"), ("bio", "Bio")]

ASSESS_SYSTEM = """You assess an imported CV for someone about to improve it, for
their stated goal. Be honest but encouraging, plain English. Return ONLY JSON:
{"summary":"<ONE sentence: overall impression / what this CV is>",
 "recommendation":"<ONE sentence: the single most valuable improvement to make>",
 "ats":<0-100>,"domain":<0-100>,"results":<0-100>,"bio":<0-100>,
 "notes":{"ats":"<=6 words","domain":"<=6 words","results":"<=6 words","bio":"<=6 words"}}
Score each 0-100 (higher = stronger):
- ats: clean structure + standard sections + role keywords so applicant-tracking
  software parses it well.
- domain: how well the content fits the TARGET role/sector. If no goal is given,
  judge general focus/coherence and say a goal will sharpen it.
- results: how much achievements are quantified with concrete outcomes (numbers,
  %, impact) vs vague duties.
- bio: strength of the profile/summary at the top — present, tailored, compelling."""


def assess_import(cv_text: str, goal: GoalSpec | None):
    """Return an Assessment scorecard for an imported CV (or None)."""
    from .models import Assessment, AssessScore
    if _client() is None or not (cv_text or "").strip():
        return None
    goal_line = (f"GOAL: {goal.raw}\nTarget: {goal.target_summary or goal.role_type} | "
                 f"sector: {goal.sector} | level: {goal.seniority}") if goal \
        else "GOAL: (not captured yet)"
    data = _complete_json(ASSESS_SYSTEM, f"{goal_line}\n\nCV:\n{cv_text[:6000]}\n\nAssess it.",
                          max_tokens=600, model=INTERVIEW_MODEL)
    if not data:
        return None
    notes = data.get("notes") or {}
    scores = []
    for key, label in ASSESS_LABELS:
        try:
            val = max(0, min(100, int(data.get(key, 0))))
        except (TypeError, ValueError):
            val = 0
        scores.append(AssessScore(key=key, label=label, score=val,
                                  note=str(notes.get(key, ""))[:40]))
    return Assessment(summary=str(data.get("summary", ""))[:240],
                      recommendation=str(data.get("recommendation", ""))[:240],
                      scores=scores)


# ---- skills ---------------------------------------------------------------
def suggest_skills(session: BuilderSession) -> List[str]:
    """Skill labels to offer for multi-select, from the goal + what they've said."""
    base = list(session.goal.suggested_skills) if session.goal else []
    if _client() is None:
        return base
    transcript = "\n".join(m.text for m in session.messages if m.role == "user")[:3000]
    sys = ("Suggest 10-16 short CV skill labels (1-3 words each) for this person, "
           "mixing skills their goal needs with ones their answers show they have. "
           "Include transferable/soft skills for early-career. Return ONLY a JSON "
           'array of strings.')
    user = (f"GOAL: {session.goal.raw if session.goal else ''}\n"
            f"THEIR ANSWERS:\n{transcript}\n\nReturn the JSON array.")
    raw = _complete(sys, user, max_tokens=400, model=EXTRACT_MODEL)
    try:
        s, e = raw.find("["), raw.rfind("]")
        arr = json.loads(raw[s:e + 1]) if s != -1 else []
        labels = [str(x)[:40] for x in arr if str(x).strip()]
        # union with goal suggestions, preserve order, dedupe case-insensitively
        seen, out = set(), []
        for x in base + labels:
            k = x.lower()
            if k not in seen:
                seen.add(k); out.append(x)
        return out[:16]
    except Exception:
        return base
