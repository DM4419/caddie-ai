"""Draft a tailored CV + cover letter + screening answers over the base docs.

Uses the Anthropic API. Every changed span is emitted as
`<mark class="chg" data-base="ORIGINAL" data-rat="WHY">new text</mark>` so the
review UI can show the diff and open the compare modal. If no API key is set (or
a call fails), we fall back to rendering the base docs unchanged so the UI still
works — drafting just won't be tailored.

The model returns the three documents separated by delimiter lines rather than
JSON: HTML is full of quotes, and embedding it in a JSON string makes the model
mis-escape and the parse fail. Delimiters are far more robust.
"""
from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

from .models import Draft

load_dotenv()

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_JD_CHARS = 6000
MAX_TOKENS = 8000

SYSTEM = """You tailor a candidate's base CV and cover letter to one specific job.

CV rules:
- Keep the base structure and every true fact. Never invent employers, dates,
  degrees, or metrics that are not in the base document.
- Tailor wording: lead with the most relevant experience, surface the metrics the
  JD cares about, and inject role keywords naturally.

COVER LETTER rules:
The base cover letter is a fixed BACKBONE: settled prose in a proven voice, with
[BRACKETED] slots to fill against this job. It is the letter style that has earned
interviews. Treat it as near-sacred.
- Keep the backbone's fixed prose and voice intact. Fill ONLY the [BRACKETED]
  slots. Adjust a fixed sentence only if the JD makes it factually inaccurate.
- Voice: warm, confident, measured; flowing sentences; British spelling; lightly
  formal but personable. ~280–330 words, five short paragraphs.
- Mirror 3–5 exact phrases from the JD, woven in naturally — never a keyword dump.
- Cover each prior role at most once. Keep experience claims numeric where the CV
  gives a number.
- NEVER invent a financing round, product fact, mutual contact, or any why-excited
  detail to fill a slot. If the needed fact was not supplied below and is not in
  the CV/JD, leave a VISIBLE PLACEHOLDER for the user instead, written as:
  <mark class="chg gap" data-base="" data-rat="needs your input">[ tell me … ]</mark>
  (replace "tell me …" with the specific thing needed, e.g.
  "[ why this company excited you ]").
- OPENER: use the opener style named below. For the CHEEKY opener, replace ONLY
  the first paragraph, then continue from "I've recently begun exploring my next
  full-time role…": state interest, then say plainly and confidently (NOT as a
  confession) that part of his search runs through an AI agent he built that
  scores new roles against how he likes to work, and this company came out near
  the top, then one honest real reason and a line tying it to what he wants next.
  NEVER use the furtive 'though I should admit how I got here' framing. Keep it
  brief; do not explain the tool.
- Forbidden: em-dash-laden or clipped punchy lines, bulleted proof sections,
  "excited to leverage", "proven track record", praise that fits any company
  ("industry leader", "exciting space"), groveling about the gap, fake-humble
  ("I just care too much"), responsibilities instead of outcomes.

Marking changes (both documents):
- Mark EVERY span you change or add from the base with:
  <mark class="chg" data-base="ORIGINAL BASE TEXT" data-rat="one-line why">new text</mark>
  For newly added text with no base equivalent, use data-base="". For an
  unfilled slot use class="chg gap" as shown above.
  Inside data-base and data-rat attribute values, NEVER use the double-quote
  character — use single quotes if you must quote something.
- Unchanged text stays as plain HTML. Use these tags EXACTLY (matching the base
  CV's markdown levels): <h3> ONLY for the candidate's name (once, at the very top);
  <div class='role-h'> for BOTH section headers (## Summary, Skills, Professional
  Experience, Education, Additional Information) AND company headers (### COMPANY —
  Location). NEVER put a company header in <h3>. Each role then follows this shape:
    <div class='role-h'>COMPANY — Location</div>
    <p><strong>Job Title</strong> | Dates | Tenure</p>
    <p><em>One-line context / domain tags</em></p>
    <ul><li>bullet</li>…</ul>
  Use <strong> for the job-title line (not <div class='role-h'>), <em> for the
  italic context line, and <ul><li> for bullets.
- Output the three documents separated by these EXACT delimiter lines, and output
  NOTHING else (no preamble, no code fences):
@@@CV@@@
(cv html)
@@@COVER@@@
(cover letter html — the filled backbone)
@@@SCREENING@@@
(If APPLICATION QUESTIONS are listed in the user message, answer EXACTLY those, in
the same order, one per <p><strong>question</strong><br>answer</p>. For a
multiple-choice question, state which option to select and one short reason; for a
free-text question, answer in the candidate's voice honoring all the rules above.
If NO questions are provided, infer 2-4 likely screening questions including 'Why
this company?', salary expectation and notice period.)
@@@END@@@"""

USER_TMPL = """JOB
Role: {role}
Company: {company}
Mode: {mode}

JOB DESCRIPTION
{jd}

COVER LETTER OPENER STYLE: {opener}

ABOUT THIS APPLICATION (use these to fill slots; where a field is blank or says
'(not supplied)', leave a visible placeholder rather than inventing):
- FRAMING ANGLE — the through-line for the whole pack; lead the CV summary, the cover
  letter, and the screening answers with this where it fits naturally: {angle}
- Why I'm excited / recent trigger: {why_excited}
- The honest gap to name: {gap}
- Cultural / working-style fit point: {cultural_fit}
- COVER-LETTER EMPHASIS — accentuate these themes and draw the clearest line from
  my experience to them; mirror the JD's wording on them, but never invent: {emphasis}
{role_fit_block}
BASE CV (markdown)
{cv}

BASE COVER LETTER BACKBONE (fill the [BRACKETED] slots, keep the rest)
{cl}
{questions_block}
Produce the tailored documents now, in the delimiter format."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _md_to_html(md: str) -> str:
    """Minimal markdown -> HTML for the no-API fallback. Headings, bullets, paras."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.DOTALL)   # drop authoring comments
    out, in_ul = [], False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            close_ul()
            continue
        if line.startswith("### "):
            close_ul(); out.append(f"<div class='role-h'>{html.escape(line[4:])}</div>")
        elif line.startswith("## "):
            close_ul(); out.append(f"<div class='role-h'>{html.escape(line[3:])}</div>")
        elif line.startswith("# "):
            close_ul(); out.append(f"<h3>{html.escape(line[2:])}</h3>")
        elif line.startswith("- "):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{html.escape(line[2:])}</li>")
        else:
            close_ul(); out.append(f"<p>{html.escape(line)}</p>")
    close_ul()
    return "\n".join(out)


def _fallback(base_cv: str, base_cl: str, error: str) -> Draft:
    return Draft(
        cv_html=_md_to_html(base_cv),
        cl_html=_md_to_html(base_cl),
        screening_html="<p class='muted'>Screening answers are generated with the "
                        "Anthropic API. Set ANTHROPIC_API_KEY to enable tailoring.</p>",
        generated_at=_now(),
        model="(none)",
        error=error,
    )


def _learning_block(strengths: bool = True) -> str:
    """Assembled learning context: distilled rules + per-application balanced examples
    + freshest raw edits (+ strengths). No single company dominates.

    Re-distils the rule set first if the raw edit log has changed since it was last
    built, so every generation reflects the user's most recent accepted edits."""
    from . import store, learndistill
    try:
        learndistill.rebuild_if_stale()
    except Exception:
        pass                              # never let a distill hiccup block drafting
    parts = []
    rules = store.read_style_rules().strip()
    if rules:
        parts.append("LEARNED RULES — follow strictly (distilled from the user's edits):\n" + rules)
    ex = store.read_style_examples_balanced(per_company=2, cap=6000).strip()
    if ex:
        parts.append("ACCEPTED EDITS — balanced examples across applications; match the voice, "
                     "do NOT copy any one company:\n" + ex)
    recent = store.read_style_recent_entries(3).strip()
    if recent:
        parts.append("MOST RECENT edits (freshest):\n" + recent)
    if not rules and not ex:
        parts.append(store.read_style_recent(20000))
    if strengths:
        s = store.read_strengths().strip()
        if s:
            parts.append("CANDIDATE STRENGTHS — treat as MET, surface where the role values them:\n" + s)
    return "\n\n".join(parts)


def rewrite_text(job_dict: dict, cv: str, current_text: str, instruction: str, kind: str = "cv") -> str:
    """Rewrite ONE passage (a CV bullet or a CL paragraph) per the user's prompt,
    honoring learnings + CV facts. Returns plain text (em-dash-free), or '' on error."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not (instruction or "").strip():
        return ""
    try:
        import anthropic
    except ImportError:
        return ""
    learn = _learning_block(strengths=True)
    unit = "bullet" if kind == "cv" else "paragraph"
    system = (
        "You rewrite a SINGLE passage of a candidate's CV or cover letter to the user's instruction. "
        "Honor these learned rules STRICTLY:\n" + learn + "\n\nHard constraints: never invent a fact, "
        "metric, or claim not supported by the CV; he is a 0-1 product LAUNCHER not a 'serial founder'; "
        "NO em-dashes; British spelling; keep it a single " + unit + " of similar length unless the "
        "instruction says otherwise. Output ONLY the rewritten text — no preamble, no quotes, no markup.")
    user = (f"HIS CV (only source of facts):\n{cv}\n\nJOB: {job_dict.get('role','')} at "
            f"{job_dict.get('company','')}\nJD:\n{(job_dict.get('description','') or '')[:MAX_JD_CHARS]}\n\n"
            f"CURRENT TEXT:\n{current_text}\n\nINSTRUCTION: {instruction}\n\nRewrite it.")
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(model=DEFAULT_MODEL, max_tokens=900, system=system,
                                     messages=[{"role": "user", "content": user}])
        out = "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception:
        return ""
    out = re.sub(r"\s+,", ",", out.replace(" — ", ", ").replace("—", ", "))   # kill em-dashes
    return out.strip().strip('"')


def generate_screening(job_dict: dict, cv: str, questions: list) -> str:
    """Answer a given list of application questions (any source) in the user's
    voice + learnings. Returns split, em-dash-clean screening HTML, or '' on error."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not questions:
        return ""
    try:
        import anthropic
    except ImportError:
        return ""
    from . import questions as q_mod
    learn = _learning_block(strengths=True)
    system = (
        "You answer a candidate's job-application questions in his voice. Honor these learned rules "
        "STRICTLY:\n" + learn + "\n\nHard constraints: never invent a fact/metric/claim not in his CV; "
        "he is a 0-1 product LAUNCHER not a 'serial founder'; NO em-dashes; British spelling; concise "
        "(~90-140 words for free-text; for multiple-choice recommend which option to select and one short "
        "why). Output one block per question, in order, as <p><strong>question</strong><br>answer</p>, and "
        "NOTHING else.")
    user = (f"HIS CV (only source of facts):\n{cv}\n\nJOB: {job_dict.get('role','')} at "
            f"{job_dict.get('company','')}\nJD:\n{(job_dict.get('description','') or '')[:MAX_JD_CHARS]}\n\n"
            f"APPLICATION QUESTIONS:\n{q_mod.format_for_prompt(questions)}")
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(model=DEFAULT_MODEL, max_tokens=2500, system=system,
                                     messages=[{"role": "user", "content": user}])
        raw = "".join(b.text for b in msg.content if b.type == "text")
    except Exception:
        return ""
    return _split_screening(_strip_prose_emdash(raw))


RESEARCH_SYSTEM = (
    "You help a candidate prepare a job application. From the JOB DESCRIPTION and what you "
    "reliably know about the COMPANY, draft short, honest prep notes that FRAME the application "
    "— the angle to lead with and the entry points that make the candidate relevant. Ground "
    "everything in the JD/company mission and the candidate's real CV — NEVER invent specific "
    "facts (no made-up funding rounds, product launches, metrics, or mutual contacts). If unsure "
    "of a concrete fact, stay general. British spelling, no em-dashes. Return ONLY JSON:\n"
    '{"angle":"1-2 sentences: the candidate\'s core relevance angle for THIS company and role — '
    'what to lead with — grounded in the JD/mission and the CV",\n'
    ' "hooks":["2-3 concrete entry points / opening angles, each a short phrase, grounded in the '
    'JD or company space (a shared focus, the problem domain, a relevant prior win) — NOT invented news"],\n'
    ' "why_excited":"1-2 sentences: a genuine, specific reason this role/company fits the candidate, '
    'tied to the JD/mission (no invented news)",\n'
    ' "cultural_fit":"1 sentence: one true working-style alignment between the candidate and this team, '
    'drawn from the JD tone + the CV",\n'
    ' "emphasis":"2-3 JD themes the candidate should accentuate and tie hardest to their experience, '
    'as a short phrase list"}')


def research_application_context(job: dict, profile: dict, cv_text: str, unmet: list,
                                 app_questions: list | None = None) -> dict:
    """Suggested 'About this application' notes drafted from the JD + company, for the
    user to review/edit. Grounded, never fabricated; the gap is taken from scoring.
    app_questions (optional): the real screening questions this employer asks — the
    framing should anticipate them so the whole pack answers what they care about."""
    import json
    # Defensive: never name something the candidate already meets as "the gap".
    real_gaps = [g for g in (unmet or [])
                 if g and not any(t in g.lower() for t in ("solid match", "strong match",
                                                            "(candidate", "already", "meets", "exceeds"))]
    out = {"angle": "", "hooks": [], "why_excited": "", "cultural_fit": "", "emphasis": "",
           "gap": (real_gaps[0] if real_gaps else ""), "error": ""}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        out["error"] = "No ANTHROPIC_API_KEY set."
        return out
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        summary = (profile.get("candidate_summary") or "").strip()
        qblock = ""
        if app_questions:
            qlist = "\n".join(f"- {q}" for q in app_questions[:12] if str(q).strip())
            if qlist:
                qblock = ("\n\nAPPLICATION QUESTIONS THIS EMPLOYER ASKS (anticipate these — the angle "
                          "and emphasis should set up strong answers to them):\n" + qlist)
        user = (f"COMPANY: {job.get('company','')}\nROLE: {job.get('role','')} "
                f"({job.get('mode','')} {job.get('location','')})\n\n"
                f"CANDIDATE SUMMARY:\n{summary}\n\nCANDIDATE CV:\n{(cv_text or '')[:2500]}\n\n"
                f"JOB DESCRIPTION:\n{(job.get('description','') or '')[:5000]}{qblock}\n\nDraft the notes now.")
        msg = client.messages.create(model=DEFAULT_MODEL, max_tokens=700,
                                     system=RESEARCH_SYSTEM,
                                     messages=[{"role": "user", "content": user}])
        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        s, e = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[s:e + 1] if s != -1 else raw)
        out["angle"] = str(data.get("angle", ""))[:500]
        hooks = data.get("hooks", [])
        out["hooks"] = [str(h).strip()[:140] for h in hooks if str(h).strip()][:4] if isinstance(hooks, list) else []
        out["why_excited"] = str(data.get("why_excited", ""))[:600]
        out["cultural_fit"] = str(data.get("cultural_fit", ""))[:400]
        emph = data.get("emphasis", "")
        out["emphasis"] = ("; ".join(emph) if isinstance(emph, list) else str(emph))[:600]
    except Exception as ex:
        out["error"] = f"Research failed: {type(ex).__name__}: {ex}"
    return out


def infer_rationale(instruction: str, before: str = "", after: str = "") -> str:
    """Turn a one-off AI-rewrite prompt (+ before/after) into a SHORT, reusable style
    rule in the candidate's voice — so the prompt itself educates the learning loop."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not (instruction or "").strip():
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        system = (
            "Infer the REUSABLE writing preference behind a candidate's one-off rewrite "
            "instruction, phrased as one short rule in his own blunt voice (e.g. 'lead with the "
            "metric, cut adjectives' or 'no em-dashes; plain verbs'). GENERALISE — drop anything "
            "specific to this one passage, company or role. British spelling. Output ONLY the rule.")
        user = (f"Rewrite instruction: {instruction}\n\nBefore:\n{(before or '')[:600]}\n\n"
                f"After:\n{(after or '')[:600]}")
        msg = client.messages.create(model=DEFAULT_MODEL, max_tokens=120, system=system,
                                     messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if b.type == "text").strip().strip('"')
    except Exception:
        return ""


def _normalize_cv_html(cv_html: str) -> str:
    """Keep the print/preview layout robust against the model's tag drift.

    Only the FIRST <h3> is the name; the print CSS styles every <h3> as the big
    centered name, so when the model emits a company header (a markdown '###') as
    <h3> instead of <div class='role-h'>, it renders as a giant centered title and
    the CV looks broken. Demote any non-first <h3> to a role/section header."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return cv_html
    soup = BeautifulSoup(cv_html or "", "html.parser")
    for h in soup.find_all("h3")[1:]:
        h.name = "div"
        h["class"] = ["role-h"]
    # A role-h whose text carries a year is really a job-title/date line, not a blue
    # rule header (section + company headers never contain a year). Make it the bold
    # title paragraph the reference layout expects.
    year = re.compile(r"\b(19|20)\d{2}\b")
    for d in soup.find_all("div", class_="role-h"):
        if year.search(d.get_text(" ", strip=True)):
            d.name = "p"
            del d["class"]
            if not d.find("strong"):                 # bold the line if it isn't already
                inner = d.decode_contents()
                d.clear()
                st = soup.new_tag("strong")
                st.append(BeautifulSoup(inner, "html.parser"))
                d.append(st)
    return str(soup) if (soup.find("h3") is not None or "role-h" in (cv_html or "")) else cv_html


def _insert_default_pagebreaks(cv_html: str) -> str:
    """Insert the user's standard page breaks: before the 2nd Professional
    Experience role, and before Education. Skipped if the CV already has breaks
    (respects manual placement)."""
    if "pagebreak" in (cv_html or ""):
        return cv_html
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return cv_html
    soup = BeautifulSoup(cv_html or "", "html.parser")
    roleh = [c for c in soup.children
             if getattr(c, "name", None) == "div" and "role-h" in (c.get("class") or [])]

    def txt(el):
        return el.get_text(" ", strip=True).lower()

    def brk():
        hr = soup.new_tag("hr"); hr["class"] = ["pagebreak"]; return hr

    inserted = False
    exp_i = next((i for i, el in enumerate(roleh)
                  if "professional experience" in txt(el) or txt(el) == "experience"), None)
    edu = next((el for el in roleh if txt(el).startswith("education")), None)
    if exp_i is not None:
        comp = []
        for el in roleh[exp_i + 1:]:
            if txt(el).startswith(("education", "additional")):
                break
            comp.append(el)
        if len(comp) >= 2:
            comp[1].insert_before(brk()); inserted = True
    if edu is not None:
        edu.insert_before(brk()); inserted = True
    return str(soup) if inserted else cv_html


def _strip_prose_emdash(htmlstr: str) -> str:
    """Remove em-dashes from prose (<p>/<li>) — a hard user rule the model keeps
    breaking. Leaves structural header separators (e.g. 'COMPANY — Location') alone."""
    try:
        from bs4 import BeautifulSoup, NavigableString
    except ImportError:
        return htmlstr
    soup = BeautifulSoup(htmlstr or "", "html.parser")
    for el in soup.find_all(["p", "li"]):
        for node in el.find_all(string=True):
            if "—" in node:
                new = node.replace(" — ", ", ").replace("—", ", ")
                new = re.sub(r"\s+,", ",", re.sub(r",\s*,", ",", new))
                node.replace_with(NavigableString(new))
    return str(soup)


def _split_screening(htmlstr: str) -> str:
    """Split each '<p><strong>Question</strong><br>answer</p>' into a non-editable
    question label + an editable answer, so the review UI only edits answers."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return htmlstr
    soup = BeautifulSoup(htmlstr or "", "html.parser")
    for p in soup.find_all("p"):
        if "sq-a" in (p.get("class") or []) or "sq-q" in (p.get("class") or []):
            continue
        strong, br = p.find("strong"), p.find("br")
        if not (strong and br):
            continue
        q_text = strong.get_text()
        after, answer = False, []
        for child in list(p.children):
            if getattr(child, "name", None) == "br":
                after = True
                continue
            if after:
                answer.append(str(child))
        wrap = soup.new_tag("div"); wrap["class"] = ["sq"]
        qd = soup.new_tag("p"); qd["class"] = ["sq-q"]
        st = soup.new_tag("strong"); st.string = q_text; qd.append(st)
        ad = soup.new_tag("p"); ad["class"] = ["sq-a"]
        ad.append(BeautifulSoup("".join(answer).strip() or "…", "html.parser"))
        wrap.append(qd); wrap.append(ad)
        p.replace_with(wrap)
    return str(soup)


def _section(text: str, start: str, end: str) -> str:
    m = re.search(re.escape(start) + r"\s*(.*?)\s*" + re.escape(end), text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _split_sections(raw: str) -> dict:
    cv = _section(raw, "@@@CV@@@", "@@@COVER@@@")
    cl = _section(raw, "@@@COVER@@@", "@@@SCREENING@@@")
    sq = _section(raw, "@@@SCREENING@@@", "@@@END@@@")
    if not sq:  # model sometimes omits the trailing @@@END@@@
        m = re.search(r"@@@SCREENING@@@\s*(.*)", raw, re.DOTALL)
        sq = m.group(1).strip() if m else ""
    if not cv:
        raise ValueError("model output missing the @@@CV@@@ section")
    return {"cv": cv, "cl": cl, "sq": sq}


def draft_documents(job_dict: dict, base_cv: str, base_cl: str,
                    app_ctx: dict | None = None, questions: list | None = None,
                    role_fit: dict | None = None) -> Draft:
    """app_ctx (optional): {opener: 'standard'|'cheeky', why_excited, gap,
    cultural_fit}. Blank fields become visible placeholders in the letter.
    questions (optional): real application questions [{text,type,options,...}] to
    answer in the Screening section; if omitted, the model infers likely ones."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback(base_cv, base_cl,
                         "No ANTHROPIC_API_KEY set — showing base documents untailored.")
    try:
        import anthropic
    except ImportError:
        return _fallback(base_cv, base_cl, "anthropic package not installed.")

    ctx = app_ctx or {}
    opener = "cheeky" if str(ctx.get("opener", "")).lower() == "cheeky" else "standard"
    if questions:
        from . import questions as q_mod
        questions_block = (
            "\nAPPLICATION QUESTIONS (from the live application form):\n"
            + q_mod.format_for_prompt(questions) + "\n"
            "Use these as follows: (a) answer them EXACTLY and in order in the @@@SCREENING@@@ section; "
            "(b) in the COVER LETTER, SUBTLY weave in the experience most relevant to the themes these "
            "questions probe — do NOT answer them explicitly or list them there, just let the letter quietly "
            "speak to what they care about; (c) in the CV, make sure the supporting points relevant to these "
            "themes are surfaced. Never invent to fit a question.\n")
    else:
        questions_block = ""
    rf = role_fit or {}
    matched = [m for m in (rf.get("matched") or []) if m]
    unmet = [u for u in (rf.get("unmet") or []) if u]
    if matched or unmet:
        role_fit_block = "\nROLE FIT (from scoring against my CV):\n"
        if matched:
            role_fit_block += ("- Surface these MATCHED strengths prominently in the CV and let the cover "
                               "letter lean on them: " + "; ".join(matched[:6]) + "\n")
        if unmet:
            role_fit_block += ("- Honest gaps for this role: " + "; ".join(unmet[:5]) + ". In the cover "
                               "letter's gap paragraph, address the most relevant one candidly and bridge "
                               "it with the closest true experience. Never hide or fake it.\n")
    else:
        role_fit_block = ""
    user = USER_TMPL.format(
        role=job_dict.get("role", ""),
        company=job_dict.get("company", ""),
        mode=job_dict.get("mode", ""),
        jd=(job_dict.get("description", "") or "")[:MAX_JD_CHARS],
        opener=opener,
        angle=(ctx.get("angle") or "").strip() or "(not supplied)",
        why_excited=(ctx.get("why_excited") or "").strip() or "(not supplied)",
        gap=(ctx.get("gap") or "").strip() or "(not supplied)",
        cultural_fit=(ctx.get("cultural_fit") or "").strip() or "(not supplied)",
        emphasis=(ctx.get("emphasis") or "").strip() or "(none — balance the JD naturally)",
        cv=base_cv,
        cl=base_cl,
        questions_block=questions_block,
        role_fit_block=role_fit_block,
    )
    # fold in learned preferences from the user's past manual edits
    lb = _learning_block(strengths=True)
    system = SYSTEM + ("\n\n" + lb if lb else "")
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(b.text for b in msg.content if b.type == "text")
        parts = _split_sections(raw)
        return Draft(
            cv_html=_insert_default_pagebreaks(_normalize_cv_html(_strip_prose_emdash(parts["cv"]))),
            cl_html=_strip_prose_emdash(parts["cl"]),
            screening_html=_split_screening(_strip_prose_emdash(parts["sq"])),
            generated_at=_now(),
            model=DEFAULT_MODEL,
        )
    except Exception as e:  # network, parse, auth — degrade to base docs
        return _fallback(base_cv, base_cl, f"Drafting failed: {type(e).__name__}: {e}")
