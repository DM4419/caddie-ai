"""LLM fit scoring: score each job 0-100 for THIS candidate, using their CV +
LinkedIn summary — not generic keywords. Reasons over title/company/domain/stage
even when the JD text is thin (many boards only store a snippet).

Batched (many jobs per call) with prompt caching of the candidate profile, so
repeat calls are cheap. Falls back to None (caller uses the keyword score) when
there's no API key or anything fails. Uses Haiku — fast and cheap for ranking.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

MODEL = os.environ.get("FIT_MODEL", "claude-haiku-4-5")
BATCH = 10
JD_CHARS = 600
CV_CHARS = 3000

DEFAULT_RUBRIC = ("Weigh qualifications/CV match first (biggest factor), then "
                  "domain fit, then stage (prefers early-stage / 0→1), then remote/UK.")


def _negative_anchors() -> str:
    from . import store
    skips = store.read_skips_recent(8000).strip()
    if not skips:
        return ""
    return ("\n\nNEGATIVE ANCHORS — the candidate has SKIPPED roles for the reasons below. "
            "Down-rank a job that shares the same off-putting traits, and reflect that in the "
            "score and the one-line reason:\n" + skips)


def _positive_anchors() -> str:
    from . import store
    s = store.read_strengths().strip()
    if not s:
        return ""
    return ("\n\nCANDIDATE STRENGTHS — treat each of these as MET; NEVER list them as unmet/gaps, and let "
            "them lift the score where the JD values them:\n" + s)


def _score_system(profile: dict) -> str:
    rubric = profile.get("fit_rubric") or DEFAULT_RUBRIC
    return ("You score how well each job fits ONE specific candidate, 0-100.\n\n"
            "HOW TO WEIGH FIT (follow strictly):\n" + rubric + _negative_anchors()
            + _positive_anchors() + "\n\n" + SCORE_FORMAT)


SCORE_FORMAT = """100 = ideal; ~50 = plausible; <30 = poor fit. Be discriminating —
spread scores across the range, don't cluster.
Return ONLY a JSON array, one object per job:
[{"i":<index>,"score":<int 0-100>,"reason":"<= 22 words why",
  "drivers":["<verbatim quote>", ...], "location":"<inferred location>",
  "unmet":["<short tag>", ...]}]
"drivers": up to 3 SHORT phrases (<= 10 words) copied VERBATIM from THAT job's
text that most drive the match to the candidate's strengths/preferences. Use an
empty list [] when the job text is too thin to quote.
"unmet": up to 3 SHORT tags (<= 5 words), each a major requirement or
qualification stated or clearly implied in THIS job's text that the candidate
does NOT meet (e.g. "10y B2B SaaS", "fintech domain", "manages 20+ reports",
"on-site in NYC"). These must be the JOB's demands the candidate falls short on
— NOT the candidate's own skills, and NOT strengths of theirs the JD omits.
Empty [] if the candidate plausibly meets the stated requirements.
"location": the job's location or region if stated OR reasonably inferable from
the text (e.g. "Remote", "Remote — US", "London, UK", "EMEA", "Berlin"). Empty ""
if there is genuinely no signal. If the role is fully location-independent remote,
return just "Remote" — do NOT append a city/region. Only attach a place when the
role is actually tied to it (a specific-country remote, hybrid, or on-site)."""


def _profile_blob(profile: dict, cv_text: str) -> str:
    summary = (profile.get("candidate_summary") or "").strip()
    cv = (cv_text or "")[:CV_CHARS]
    return f"CANDIDATE — LinkedIn summary:\n{summary}\n\nCANDIDATE — CV:\n{cv}"


def _parse(text: str) -> list:
    text = text.strip()
    s, e = text.find("["), text.rfind("]")
    if s != -1 and e != -1:
        text = text[s:e + 1]
    return json.loads(text)


def _analysis_system(profile: dict) -> str:
    rubric = profile.get("fit_rubric") or DEFAULT_RUBRIC
    return ("You analyze fit between a candidate and ONE job, for the candidate. "
            "Be specific, concrete, and honest. The fit score was assigned using "
            "this rubric — stay consistent with it:\n" + rubric + _positive_anchors()
            + "\n\n" + ANALYSIS_FORMAT)


ANALYSIS_FORMAT = """Return ONLY a JSON object:
{"score_rationale":"1-2 sentences explaining why the assigned fit score is what it
   is — the main things that lifted it and the main things that held it back",
 "best_fit":"2-4 sentences: where this role fits the candidate well and why",
 "shortcomings":"2-4 sentences: where it's a weak fit, risks, or gaps, and why",
 "skills_matched":["<exact skill from the provided candidate-skills list that this
   role clearly values or requires>", ...],
 "unmet":["<a requirement the JD ASKS FOR that the candidate clearly does NOT
   have — direction is JD→candidate>", ...],
 "breakdown":[{"label":"Qualifications","score":<0-100>,"note":"<= 8 words"},
   {"label":"Domain","score":<0-100>,"note":"<= 8 words"},
   {"label":"Role & stage","score":<0-100>,"note":"<= 8 words"},
   {"label":"Remote / location","score":<0-100>,"note":"<= 8 words"}]}
Only include skills that genuinely apply; judge from the role + domain, not just
literal word matches. "unmet": up to 6 items (each <= 12 words). Each MUST be a
qualification / skill / experience / must-have EXPLICITLY present in the JD text —
you must be able to point to the JD phrase it comes from. NEVER invent or
over-infer a requirement the JD does not state (e.g. do NOT add "validator /
node-operator economics" or "consensus protocol expertise" unless those exact
ideas appear in the JD). It must be something the candidate clearly LACKS AND that
is NOT already covered by the CANDIDATE STRENGTHS listed above (if a strength
covers it, it is MET — never list it). Examples: "10+ yrs B2B fintech PM",
"managed 50+ eng", "must be onsite in NYC". Scan the JD's requirements; list only
the ones the CV does not satisfy. Do NOT list the
candidate's own preferences, strengths the JD merely omits, or company/comp gripes
(NOT "no evidence they value founders", NOT "comp below market", NOT "company
doesn't specialise in Voice AI"). [] if the candidate plausibly meets everything.
"breakdown": score the candidate's TRUE standing on each dimension 0-100, judged
semantically from their CV + summary (NOT literal keyword matching) — e.g. a deep
0→1 AI builder scores high on Qualifications even if the JD never uses those words."""


def analyze(job: dict, profile: dict, cv_text: str, score: int = None) -> dict:
    """On-demand deep fit analysis for one job (score rationale, best-fit,
    shortcomings, semantic skills)."""
    skills = profile.get("skills", [])
    result = {"score_rationale": "", "best_fit": "", "shortcomings": "",
              "breakdown": [], "skills_matched": [], "skills_all": skills,
              "unmet": [], "error": "", "generated_at": _now()}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        result["error"] = "No ANTHROPIC_API_KEY set — fit analysis unavailable."
        return result
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        score_line = f"Assigned fit score: {score}/100\n" if score is not None else ""
        user = (f"CANDIDATE SKILLS LIST: {skills}\n\n"
                f"JOB\nRole: {job.get('role','')}\nCompany: {job.get('company','')}\n"
                f"Location: {job.get('location','')}  Mode: {job.get('mode','')}\n"
                f"{score_line}"
                f"Description:\n{(job.get('description','') or '')[:3000]}\n\n"
                "Analyze the fit now.")
        msg = client.messages.create(
            model=MODEL, max_tokens=1500,
            system=[
                {"type": "text", "text": _analysis_system(profile)},
                {"type": "text", "text": _profile_blob(profile, cv_text),
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        s, e = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[s:e + 1] if s != -1 else raw)
        result["score_rationale"] = str(data.get("score_rationale", ""))[:600]
        result["best_fit"] = str(data.get("best_fit", ""))[:1200]
        result["shortcomings"] = str(data.get("shortcomings", ""))[:1200]
        matched = data.get("skills_matched") or []
        # keep only real profile skills, case-insensitive
        low = {s.lower(): s for s in skills}
        result["skills_matched"] = [low[m.lower()] for m in matched if m.lower() in low]
        result["unmet"] = [str(u)[:120] for u in (data.get("unmet") or [])
                           if str(u).strip()][:6]
        for d in (data.get("breakdown") or [])[:6]:
            if str(d.get("label", "")).strip():
                result["breakdown"].append({
                    "label": str(d.get("label", ""))[:40],
                    "score": int(max(0, min(100, d.get("score", 0)))),
                    "note": str(d.get("note", ""))[:60]})
    except Exception as e:
        result["error"] = f"Analysis failed: {type(e).__name__}: {e}"
    return result


REQ_SYSTEM = """You assess a job's requirements against ONE candidate (CV +
summary). Find each concrete requirement / qualification in the JD and classify
how well the candidate meets it. Return ONLY JSON:
{"requirements":[{"quote":"<verbatim phrase copied EXACTLY from the JD>",
   "level":"match|stretch|mismatch","note":"<= 10 words why"}]}
- "match": the candidate clearly meets it.
- "stretch": partial, adjacent, or arguable.
- "mismatch": the candidate clearly lacks it.
Extract 5-15 requirements. The "quote" MUST be copied verbatim from the JD text
(so it can be highlighted in place); do not paraphrase."""


def classify_requirements(jd_text: str, profile: dict, cv_text: str) -> dict:
    """Classify each JD requirement vs the candidate -> {requirements, error}."""
    out = {"requirements": [], "error": ""}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        out["error"] = "No ANTHROPIC_API_KEY set."
        return out
    if not (jd_text or "").strip():
        out["error"] = "No JD text to assess."
        return out
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=MODEL, max_tokens=1800,
            system=[
                {"type": "text", "text": REQ_SYSTEM + _positive_anchors()},
                {"type": "text", "text": _profile_blob(profile, cv_text),
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user",
                       "content": f"JOB DESCRIPTION:\n{jd_text[:6000]}\n\nAssess now."}],
        )
        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        s, e = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[s:e + 1] if s != -1 else raw)
        valid = {"match", "stretch", "mismatch"}
        for r in (data.get("requirements") or []):
            q = str(r.get("quote", "")).strip()
            lvl = str(r.get("level", "stretch")).lower()
            if q and lvl in valid:
                out["requirements"].append(
                    {"quote": q[:200], "level": lvl, "note": str(r.get("note", ""))[:80]})
    except Exception as e:
        out["error"] = f"Requirement assessment failed: {type(e).__name__}: {e}"
    return out


def score_batch(raws: list, profile: dict, cv_text: str):
    """Return [{score, reason}] aligned to raws, or None if unavailable."""
    if not raws:
        return []
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic(api_key=api_key)
    blob = _profile_blob(profile, cv_text)
    out = [None] * len(raws)
    any_ok = False
    for start in range(0, len(raws), BATCH):
        chunk = raws[start:start + BATCH]
        jobs_txt = "\n".join(
            f'{i}. {r.get("role", "")} @ {r.get("company", "")} '
            f'[{r.get("mode", "")}] — {(r.get("description", "") or "")[:JD_CHARS]}'
            for i, r in enumerate(chunk))
        # per-batch + one retry, so a single transient failure doesn't wipe the run
        for attempt in range(2):
            try:
                msg = client.messages.create(
                    model=MODEL,
                    max_tokens=3200,        # 10 jobs × (reason+drivers+location+unmet)
                    system=[
                        {"type": "text", "text": _score_system(profile)},
                        {"type": "text", "text": blob,
                         "cache_control": {"type": "ephemeral"}},
                    ],
                    messages=[{"role": "user", "content": f"Score these jobs:\n{jobs_txt}"}],
                )
                raw = "".join(b.text for b in msg.content if b.type == "text")
                for item in _parse(raw):
                    i = item.get("i")
                    if isinstance(i, int) and 0 <= i < len(chunk):
                        drivers = item.get("drivers") or []
                        out[start + i] = {
                            "score": int(max(0, min(100, item.get("score", 0)))),
                            "reason": str(item.get("reason", ""))[:160],
                            "drivers": [str(d)[:90] for d in drivers if str(d).strip()][:3],
                            "location": str(item.get("location", ""))[:60].strip(),
                            "unmet": [str(u)[:48] for u in (item.get("unmet") or [])
                                      if str(u).strip()][:3],
                        }
                any_ok = True
                break
            except Exception:
                if attempt == 0:
                    time.sleep(3)        # brief backoff, then retry this batch once
                # else: leave this batch's entries None -> keyword fallback for them
    return out if any_ok else None
