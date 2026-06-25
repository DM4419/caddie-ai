"""FastAPI backend for Phase 0.

Serves the static UI and the JSON API behind it:
  GET  /                         -> the single-page UI
  GET  /api/jobs                 -> ranked job list (from the CSV tracker source)
  POST /api/jobs/paste           -> {url?, text?} -> fetch + score + store
  GET  /api/jobs/{id}            -> full job incl. cached draft (if any)
  POST /api/jobs/{id}/draft      -> generate tailored CV/CL/screening
  POST /api/jobs/{id}/status     -> {status}
  GET  /api/profile              -> weights
  POST /api/profile/weights      -> {weights} -> save + re-score all
  GET  /api/base-docs            -> upload status + current text
  POST /api/base-docs/{kind}     -> upload base CV/CL (file or pasted text)
  GET  /api/applications.csv     -> download the tracker
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from adapters import (adzuna, arbeitnow, ats, browser, cryptocurrencyjobs,
                      dynamitejobs, getro, googlejobs, himalayas, listing,
                      productbuilderjobs, remoteok, remotive, theirstack, web3career,
                      workingnomads)
from engine import clchanges
from engine import draft as draft_mod
from engine import docxcv
from engine import extract
from engine import fetch as fetch_mod
from engine import boards_optimize, fitscore, liveness, pipeline, questions, score as score_mod, store
from engine.cvbuilder import engine as cveng, render as cvrender, store as cvstore
from engine.cvbuilder.models import ChatMessage
from engine.models import Analysis, JDDoc, Requirement

# Board id -> fetcher module (boards that support bulk preview/import).
BOARD_FETCHERS = {
    "productbuilderjobs": productbuilderjobs,
    "remoteok": remoteok,
    "remotive": remotive,
    "himalayas": himalayas,
    "arbeitnow": arbeitnow,
    "theirstack": theirstack,
    "workingnomads": workingnomads,
    "web3career": web3career,
    "adzuna": adzuna,
    "cryptocurrencyjobs": cryptocurrencyjobs,
    "dynamitejobs": dynamitejobs,
    "googlejobs": googlejobs,
}

app = FastAPI(title="Job Applicator")
STATIC = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def _startup() -> None:
    store.ensure_dirs()
    store.rewrite_csv()


@app.middleware("http")
async def _no_cache(request, call_next):
    """Force revalidation of the UI/static assets so edits show up immediately."""
    resp = await call_next(request)
    if request.url.path in ("/", "/cv-builder") or request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text())


# ---- CV builder (self-contained module) ----------------------------------
@app.get("/cv-builder", response_class=HTMLResponse)
def cv_builder_page() -> HTMLResponse:
    return HTMLResponse((STATIC / "cvbuilder.html").read_text())


def _cvb_payload(session) -> dict:
    return {"id": session.id, "step": session.step,
            "goal": session.goal.model_dump() if session.goal else None,
            "assessment": session.assessment.model_dump() if session.assessment else None,
            "messages": [m.model_dump() for m in session.messages],
            "cv": session.cv.model_dump(),
            "cv_html": cvrender.render_html(session.cv)}


def _cvb_get_or_404(session_id: str):
    s = cvstore.get(session_id)
    if not s:
        raise HTTPException(404, "CV-builder session not found")
    return s


@app.post("/api/cvbuilder/session")
def cvb_new() -> dict:
    s = cvstore.new_session()
    s.messages.append(ChatMessage(role="assistant", text=(
        "Hi! I'll help you build a strong CV, step by step — no experience needed. "
        "First, in your own words: what kind of role, course, or apprenticeship are "
        "you hoping to apply for?")))
    cvstore.save(s)
    return _cvb_payload(s)


@app.get("/api/cvbuilder/{session_id}")
def cvb_get(session_id: str) -> dict:
    return _cvb_payload(_cvb_get_or_404(session_id))


class CvbGoalIn(BaseModel):
    goal: str


@app.post("/api/cvbuilder/{session_id}/goal")
def cvb_goal(session_id: str, body: CvbGoalIn) -> dict:
    s = _cvb_get_or_404(session_id)
    if not body.goal.strip():
        raise HTTPException(400, "Tell me what you're aiming for.")
    s.goal = cveng.infer_goal(body.goal.strip())
    s.step = "interview"
    s.messages.append(ChatMessage(role="user", text=body.goal.strip()))
    if s.imported_text:                       # assess an already-imported CV now we know the goal
        s.assessment = cveng.assess_import(s.imported_text, s.goal)
        s.cv = cveng.extract_cv(s)
    s.messages.append(ChatMessage(role="assistant", text=cveng.interview_turn(s)))
    cvstore.save(s)
    return _cvb_payload(s)


class CvbMessageIn(BaseModel):
    text: str


@app.post("/api/cvbuilder/{session_id}/message")
def cvb_message(session_id: str, body: CvbMessageIn) -> dict:
    s = _cvb_get_or_404(session_id)
    if not body.text.strip():
        raise HTTPException(400, "Empty message.")
    s.messages.append(ChatMessage(role="user", text=body.text.strip()))
    if not s.goal:                            # first message is their goal
        s.goal = cveng.infer_goal(body.text.strip())
        s.step = "interview"
    else:
        s.cv = cveng.extract_cv(s)            # keep the live preview current
    s.messages.append(ChatMessage(role="assistant", text=cveng.interview_turn(s)))
    cvstore.save(s)
    return _cvb_payload(s)


class CvbImportIn(BaseModel):
    text: str = ""


@app.post("/api/cvbuilder/{session_id}/import")
def cvb_import(session_id: str, body: CvbImportIn) -> dict:
    s = _cvb_get_or_404(session_id)
    if not body.text.strip():
        raise HTTPException(400, "Paste your CV text to import it.")
    s.imported_text = body.text.strip()[:20000]
    _cvb_apply_import(s)
    cvstore.save(s)
    return _cvb_payload(s)


@app.post("/api/cvbuilder/{session_id}/import-file")
async def cvb_import_file(session_id: str, file: UploadFile) -> dict:
    s = _cvb_get_or_404(session_id)
    try:
        text = extract.extract_text(await file.read(), file.filename or "")
    except extract.ExtractError as e:
        raise HTTPException(400, str(e))
    if not (text or "").strip():
        raise HTTPException(400, "Couldn't read any text from that file — if it's a scan/image, paste the text instead.")
    s.imported_text = text.strip()[:20000]
    _cvb_apply_import(s)
    cvstore.save(s)
    return _cvb_payload(s)


def _cvb_apply_import(s) -> None:
    """Assess + prefill the CV from the imported text — works with or without a
    goal yet (so importing first, before answering anything, still does something)."""
    s.assessment = cveng.assess_import(s.imported_text, s.goal)
    s.cv = cveng.extract_cv(s)
    note = "Thanks — I've read your CV and filled in what I could on the right."
    if not s.goal:
        note += " Now tell me: what role, course, or apprenticeship are you aiming for?"
    s.messages.append(ChatMessage(role="assistant", text=note))


@app.get("/api/cvbuilder/{session_id}/skills/suggest")
def cvb_skills_suggest(session_id: str) -> dict:
    return {"skills": cveng.suggest_skills(_cvb_get_or_404(session_id))}


class CvbSkillsIn(BaseModel):
    skills: list


@app.post("/api/cvbuilder/{session_id}/skills")
def cvb_skills_set(session_id: str, body: CvbSkillsIn) -> dict:
    s = _cvb_get_or_404(session_id)
    s.cv.skills = [str(x)[:40] for x in body.skills if str(x).strip()][:30]
    cvstore.save(s)
    return _cvb_payload(s)


@app.get("/api/cvbuilder/{session_id}/cv.html", response_class=HTMLResponse)
def cvb_cv_html(session_id: str) -> HTMLResponse:
    # editable=False -> clean print output: no placeholders/edit affordances.
    return HTMLResponse(cvrender.render_html(_cvb_get_or_404(session_id).cv, editable=False))


_CVB_ENTRY_SECTIONS = {"experience", "education", "projects", "volunteering"}
_CVB_TOP_FIELDS = {"name", "headline", "email", "phone", "location", "summary"}
_CVB_ENTRY_FIELDS = {"title", "org", "location", "start", "end", "summary"}


class CvbFieldIn(BaseModel):
    section: str = ""          # "" -> a top-level CV field; else an entry list
    index: int = -1            # entry index within that section
    field: str
    value: str = ""


@app.post("/api/cvbuilder/{session_id}/field")
def cvb_field(session_id: str, body: CvbFieldIn) -> dict:
    """Update ONE field from an inline edit on the CV preview."""
    s = _cvb_get_or_404(session_id)
    v = body.value.strip()[:600 if body.field in ("summary", "headline") else 300]
    if not body.section:                       # top-level scalar (name, contact, summary)
        if body.field in _CVB_TOP_FIELDS:
            setattr(s.cv, body.field, v)
    elif body.section in _CVB_ENTRY_SECTIONS:
        lst = getattr(s.cv, body.section)
        if 0 <= body.index < len(lst):
            e = lst[body.index]
            if body.field == "dates":          # one editable date field -> store verbatim
                e.start, e.end = "", v
            elif body.field in _CVB_ENTRY_FIELDS:
                setattr(e, body.field, v)
    cvstore.save(s)
    return _cvb_payload(s)


# ---- jobs ----------------------------------------------------------------
@app.get("/api/jobs")
def get_jobs(archived: bool = False, q: str = "") -> dict:
    base = store.base_status()
    all_jobs = store.list_jobs()
    ql = q.strip().lower()
    if ql:
        # search EVERYTHING — every status and archived — by role/company/title/location
        src = [j for j in all_jobs if ql in (j.role or "").lower()
               or ql in (j.company or "").lower() or ql in (j.title or "").lower()
               or ql in (j.location or "").lower()]
    else:
        src = [j for j in all_jobs if bool(j.archived) == archived]
    jobs = [j.model_dump(exclude={"description", "factors", "draft"}) for j in src]
    archived_count = sum(1 for j in all_jobs if j.archived)
    active = [j for j in all_jobs if not j.archived]
    FOUNDER = {"eir", "zero_to_one", "founder_welcome"}
    untriaged = lambda j: j.status not in ("applied", "interview", "rejected", "skipped")
    counts = {
        "applied": sum(1 for j in active if j.status == "applied"),
        "interview": sum(1 for j in active if j.status == "interview"),
        "rejected": sum(1 for j in active if j.status == "rejected"),
        "skipped": sum(1 for j in active if j.status == "skipped"),
        "founder": sum(1 for j in active if untriaged(j) and FOUNDER.intersection(j.flags or [])),
        "voice": sum(1 for j in active if untriaged(j) and "voice_ai" in (j.flags or [])),
        "bookmarked": sum(1 for j in active if j.bookmarked),
    }
    # source board -> domain, so the UI can show each board's favicon
    sources = {}
    for b in store.load_boards():
        host = urlparse(b.get("url", "")).netloc.replace("www.", "")
        if b.get("name") and host:
            sources[b["name"]] = host
    return {"jobs": jobs, "base": base, "archived_count": archived_count,
            "counts": counts, "sources": sources, "search": ql}


class PasteIn(BaseModel):
    url: str = ""
    text: str = ""


@app.post("/api/jobs/paste")
def paste(body: PasteIn) -> dict:
    if not body.url.strip() and not body.text.strip():
        raise HTTPException(400, "Provide a job URL or paste a description.")
    try:
        job = pipeline.add_from_paste(url=body.url, text=body.text)
    except Exception as e:
        raise HTTPException(502, f"Could not fetch that URL: {e}")
    dead = liveness.looks_dead(job.description)
    if dead:
        store.delete_job(job.id)
        raise HTTPException(400, f"That posting looks filled/closed (“{dead}”) — not added.")
    return {"job": job.model_dump(exclude={"description", "factors", "draft"})}


# Boards are scanned in PREFERENCE order so that when the same role appears on
# several boards, the version from the preferred board is imported first and the
# later (e.g. Adzuna) duplicate is skipped by the content dedupe. Adzuna last.
REFRESH_ORDER = ["productbuilderjobs", "remoteok", "workingnomads",
                 "cryptocurrencyjobs", "web3career", "adzuna"]


# Live state for the running scan, polled by GET /api/jobs/refresh/status.
_refresh_lock = threading.Lock()
_refresh_state = {"running": False, "done": True, "boards": [], "totals": {}, "since": ""}


def _board_fetchable(b: dict) -> bool:
    return bool(_board_id(b) in BOARD_FETCHERS
                or (b.get("provider") and b.get("slug"))
                or (b.get("tier") in ("listing", "browser") and b.get("url")))


def _refresh_order() -> list:
    # full refresh = EVERY enabled fetchable board (API feeds, ATS companies,
    # recruiters, listing, browser). Fast preferred feeds first, slow browser last.
    boards = [b for b in store.load_boards() if b.get("enabled", True) and _board_fetchable(b)]
    ids = [_board_id(b) for b in boards]
    pref = [b for b in REFRESH_ORDER if b in ids]
    browser_ids = [_board_id(b) for b in boards if b.get("tier") == "browser"]
    middle = [b for b in ids if b not in pref and b not in browser_ids]
    return pref + middle + browser_ids


def _set_board(i: int, **fields) -> None:
    with _refresh_lock:
        _refresh_state["boards"][i].update(fields)


def _run_refresh(only_ids: set = None) -> None:
    from datetime import date
    profile = store.load_profile()
    regs = {_board_id(b): b for b in store.load_boards()}
    if only_ids is not None:                         # scope to an explicit subset (group scan)
        order = [bid for bid in regs if bid in only_ids]
    else:
        order = _refresh_order()
    names = {bid: b.get("name", bid) for bid, b in regs.items()}
    today = date.today().isoformat()
    with _refresh_lock:
        _refresh_state.update(
            running=True, done=False, since="per board",
            boards=[{"id": b, "name": names.get(b, b.title()), "status": "queued",
                     "imported": 0, "error": "", "since": ""} for b in order],
            totals={"imported": 0, "skipped": 0, "dropped": 0, "stale": 0, "archived": 0})
    imported = skipped = dropped = stale = good = 0
    for i, bid in enumerate(order):
        fetcher = _resolve_fetcher(bid)
        if not fetcher:
            _set_board(i, status="skipped")
            continue
        # ATS boards list only currently-open roles, so recency filtering hides
        # still-open targets — import them all (geo + role gate still apply).
        rb = regs.get(bid, {})
        if rb.get("provider") and rb.get("slug"):
            since = "2000-01-01"
        else:
            since = pipeline.board_since(bid, profile)   # first scan 7d, then incremental
        _set_board(i, status="in_progress", since=since)
        try:
            qb = getattr(fetcher, "QUERY_BASED", False)
            ps, pg = (20, 1) if qb else (50, 2)
            res = pipeline.board_fetch(fetcher, profile, page_size=ps, pages=pg,
                                       board_id=bid, since=since)
            theirstack.record_fetched(res["jobs"])   # advance incremental watermark (no-op for other boards)
            imp, sk, dr, st, gd = pipeline.import_raws(res["jobs"], since=since)
            imported += imp; skipped += sk; dropped += dr; stale += st; good += gd
            store.set_board_scan(bid, today)         # stamp this board's scan
            _set_board(i, status="done", imported=imp)
        except Exception as e:
            _set_board(i, status="error", error=f"{type(e).__name__}: {e}"[:160])
    archived = pipeline.archive_stale(profile)
    # Fetch full JDs for the high scorers (AI or WT >= threshold) so the geo + language
    # gates run upfront — a thin snippet often hides a US-only / C2-language requirement.
    enr = enrich_high_scorers(int(profile.get("jd_fetch_threshold", 70)))
    archived += enr["archived"]
    store.set_last_fetch(today)
    with _refresh_lock:
        _refresh_state.update(running=False, done=True, totals={
            "imported": imported, "skipped": skipped, "dropped": dropped,
            "stale": stale, "archived": archived, "good": good, "enriched": enr["enriched"]})


@app.post("/api/jobs/refresh")
def refresh_jobs() -> dict:
    """Kick off a board scan in the background; poll /refresh/status for progress.

    Incremental: fetches jobs posted since the last fetch (minus 1 day); on the
    first ever run, uses the recency window (recency_days).
    """
    with _refresh_lock:
        if _refresh_state.get("running"):
            return dict(_refresh_state)
    threading.Thread(target=_run_refresh, daemon=True).start()
    time.sleep(0.4)                          # let the thread publish the queued list
    with _refresh_lock:
        return dict(_refresh_state)


@app.get("/api/jobs/refresh/status")
def refresh_status() -> dict:
    with _refresh_lock:
        return dict(_refresh_state)


class GroupScanIn(BaseModel):
    group: str


@app.post("/api/boards/group-scan")
def group_scan(body: GroupScanIn) -> dict:
    """Scan only the boards in a group (e.g. 'Direct Employers'), in the
    background — same status feed as a full refresh."""
    ids = {_board_id(b) for b in store.load_boards() if b.get("group") == body.group}
    if not ids:
        raise HTTPException(404, f"No boards in group “{body.group}”.")
    with _refresh_lock:
        if _refresh_state.get("running"):
            return dict(_refresh_state)
    threading.Thread(target=_run_refresh, args=(ids,), daemon=True).start()
    time.sleep(0.4)
    with _refresh_lock:
        return dict(_refresh_state)


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status == "new":          # opening it counts as "checked"
        job.status = "review"
        store.save_job(job)
    cvs = store.list_app_cvs()
    # Flag an old-format analysis whose skills came from the fixed candidate list
    # (now they're scraped from the JD) so the client recomputes it once.
    analysis_stale = False
    if job.analysis and (job.analysis.skills_all or []):
        prof_skills = {str(s).strip().lower() for s in (store.load_profile().get("skills") or [])}
        cur = {str(s).strip().lower() for s in job.analysis.skills_all}
        analysis_stale = bool(prof_skills) and cur.issubset(prof_skills)
    # ATS optimisation is part of building the pack, not browsing the job: only
    # surface the board tip once a draft exists (it survives reloads this way).
    return {"job": job.model_dump(), "analysis_stale": analysis_stale,
            "board": boards_optimize.ui_tip(job.url) if job.draft else None,
            "cv_options": [{"id": c["id"], "name": c["name"]} for c in cvs]}


@app.get("/api/style")
def get_style() -> dict:
    import re
    from engine import learndistill
    try:
        learndistill.rebuild_if_stale()      # keep the distilled rules current for review
    except Exception:
        pass
    text = store.read_style()
    parts = re.split(r"(?m)^(?=## )", text)
    preamble = parts[0] if parts and not parts[0].startswith("## ") else ""
    entries = [p.rstrip() for p in parts if p.startswith("## ")]
    return {"preamble": preamble, "entries": entries,
            "rules": store.read_style_rules(),   # condensed, inferred guidelines/guardrails
            "recent": entries[-3:][::-1]}        # last few edits that shaped them (newest first)


class StyleIn(BaseModel):
    text: str = ""


@app.post("/api/style")
def save_style(body: StyleIn) -> dict:
    store.write_style(body.text)
    return {"ok": True}


class OverrideIn(BaseModel):
    base: str = ""
    suggested: str = ""
    actual: str = ""
    reason: str = ""
    instruction: str = ""     # the AI-rewrite prompt, if this edit came from one


@app.post("/api/jobs/{job_id}/override")
def save_override(job_id: str, body: OverrideIn) -> dict:
    """Record a manual CV edit + rationale into style.md so future drafts learn
    it. Never edits the base CV or this draft's other applications. When the edit
    came from an AI-rewrite prompt and no explicit reason was given, the reusable
    rationale is inferred from that prompt."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    reason = (body.reason or "").strip()
    if not reason and body.instruction.strip():
        reason = draft_mod.infer_rationale(body.instruction, body.base, body.actual)
    store.append_style_entry(job.role, job.company, body.base, body.suggested,
                             body.actual, reason)
    return {"ok": True, "reason": reason}


class DraftIn(BaseModel):
    cv_id: str = ""
    opener: str = ""          # ""=auto by flags | standard | cheeky
    why_excited: str = ""     # recent trigger / what pushed you to apply
    gap: str = ""             # the honest JD gap to name
    cultural_fit: str = ""    # one true working-style match point
    emphasis: str = ""        # free-text: JD themes to accentuate in the letter
    angle: str = ""           # framing through-line for the whole pack (from Research)
    hooks: list = []          # research entry-points (display), persisted with the pack
    extra_context: str = ""   # optional user-supplied notes + links to enrich the CL/answers
    req_evidence: dict = {}   # JD requirement quote -> real metric/fact (Strengthen your match)
    req_cl: dict = {}         # JD requirement quote -> include the bullet in the cover letter (bool)
    req_employer: dict = {}   # JD requirement quote -> which CV employer the bullet belongs under


_CTX_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


def _expand_context(text: str) -> str:
    """Candidate-supplied context: their notes verbatim, plus the extracted text of any
    URLs they dropped in (so a linked project/article actually informs the draft)."""
    text = (text or "").strip()
    if not text:
        return ""
    parts, fetched = [text], 0
    for url in dict.fromkeys(_CTX_URL_RE.findall(text)):
        if fetched >= 4:                       # cap fetches per pack
            break
        try:
            body = (fetch_mod.fetch(url) or {}).get("description", "")
            if body:
                parts.append(f"\n[Content from {url}]:\n{body[:2500]}")
                fetched += 1
        except Exception:
            pass
    return "\n".join(parts)[:6000]


# Flags marking AI-forward / builder cultures where the cheeky opener fits.
CHEEKY_FLAGS = {"voice_ai", "eir", "zero_to_one", "founder_welcome"}


def _suggest_opener(job) -> str:
    return "cheeky" if (set(job.flags or []) & CHEEKY_FLAGS) else "standard"


@app.post("/api/jobs/{job_id}/draft")
def make_draft(job_id: str, body: Optional[DraftIn] = None) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    cvs = store.list_app_cvs()
    chosen = None
    if body and body.cv_id:
        chosen = next((c for c in cvs if c["id"] == body.cv_id), None)
    if not chosen and cvs:
        chosen = _suggest_app_cv(job)        # auto-suggest by the job's flags
    base_cv = store.get_app_cv_text(chosen["id"]) if chosen else store.read_base_cv()

    opener = (body.opener if body and body.opener in ("standard", "cheeky")
              else _suggest_opener(job))
    # Prefer questions already fetched earlier in the build (so the same set that
    # shaped research conditions the draft); fall back to fetching now.
    real_qs = job.questions or (questions.fetch_questions(job.url) if job.url else [])
    if real_qs and not job.questions:
        job.questions = real_qs
    # persist Strengthen-your-match state (edited bullets + cover-letter inclusion)
    if body and body.req_evidence:
        job.req_evidence = {**(job.req_evidence or {}),
                            **{k: v for k, v in body.req_evidence.items() if (v or "").strip()}}
    if body and body.req_cl:
        job.req_cl = {**(job.req_cl or {}), **{k: bool(v) for k, v in body.req_cl.items()}}
    if body and body.req_employer and job.jd:        # user re-assigned a bullet to a CV role
        for r in job.jd.requirements:
            emp = body.req_employer.get(r.quote)
            if emp:
                r.draft_employer = emp
    # build the structured requirement->bullet map (text + CV employer + CL flag) for the draft
    req_map = []
    for r in (job.jd.requirements if job.jd else []):
        if r.level not in ("match", "stretch"):
            continue
        text = (job.req_evidence or {}).get(r.quote) or r.draft_point
        if not (text or "").strip():
            continue
        req_map.append({"requirement": r.quote, "point": text, "employer": r.draft_employer,
                        "cl": bool((job.req_cl or {}).get(r.quote, False))})
    draft = draft_mod.draft_documents(
        {"role": job.role, "company": job.company, "mode": job.mode,
         "description": job.description, "url": job.url},
        base_cv, store.read_base_cl(),
        app_ctx={"opener": opener,
                 "angle": body.angle if body else "",
                 "why_excited": body.why_excited if body else "",
                 "gap": body.gap if body else "",
                 "cultural_fit": body.cultural_fit if body else "",
                 "emphasis": body.emphasis if body else "",
                 "extra_context": _expand_context(body.extra_context if body else ""),
                 "req_map": req_map},
        questions=real_qs,
        role_fit={"matched": job.drivers, "unmet": job.unmet},
    )
    draft.cv_used = ({"id": chosen["id"], "name": chosen["name"]} if chosen
                     else {"id": "", "name": "Matching CV (no variants yet)"})
    # Persist the research/framing with the pack so the Research tab repopulates
    # after a reload (it lived only in client memory before -> empty on refresh).
    draft.ctx = {"opener": (body.opener if body else "") or "",
                 "angle": body.angle if body else "",
                 "why_excited": body.why_excited if body else "",
                 "gap": body.gap if body else "",
                 "cultural_fit": body.cultural_fit if body else "",
                 "emphasis": body.emphasis if body else "",
                 "extra_context": (body.extra_context if body else "") or "",
                 "hooks": (body.hooks if body and body.hooks else [])}
    job.draft = draft
    job.archived = False        # building an application = actively pursuing it -> rescue from Archived
    store.save_job(job)
    return {"draft": draft.model_dump(),
            "opener_used": opener,
            "questions_fetched": len(real_qs),
            "cv_used": {"id": chosen["id"], "name": chosen["name"]} if chosen
                       else {"id": "", "name": "Matching CV (no variants yet)"},
            "cv_options": [{"id": c["id"], "name": c["name"]} for c in cvs]}


class DraftEditIn(BaseModel):
    cv_html: Optional[str] = None
    cl_html: Optional[str] = None
    screening_html: Optional[str] = None


@app.post("/api/jobs/{job_id}/draft-edit")
def save_draft_edit(job_id: str, body: DraftEditIn) -> dict:
    """Persist the user's in-place edits to a generated draft (titles, sections,
    wording) so they survive reload and flow into export/PDF."""
    job = store.get_job(job_id)
    if not job or not job.draft:
        raise HTTPException(404, "No draft to edit")
    if body.cv_html is not None:
        job.draft.cv_html = body.cv_html
    if body.cl_html is not None:
        job.draft.cl_html = body.cl_html
    if body.screening_html is not None:
        job.draft.screening_html = body.screening_html
    store.save_job(job)
    return {"ok": True}


class DocxIn(BaseModel):
    html: str = ""
    label: str = "CV"        # CV | Cover letter | Screening — used for the filename


@app.post("/api/jobs/{job_id}/export.docx")
def export_docx(job_id: str, body: DocxIn) -> Response:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    html = body.html or (job.draft.cv_html if job.draft else "")
    if not html.strip():
        raise HTTPException(400, "Nothing to export — generate a draft first.")
    data = docxcv.html_to_docx(html)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{_export_basename(body.label, job.role)}.docx"'},
    )


def _export_basename(label: str, role: str) -> str:
    """<DocType>_<Name>_<Role>, e.g. CV_Alex_Rivera_Product_Manager."""
    cv = store.read_base_cv()
    m = re.search(r"^#\s+(.+)$", cv, re.M)
    name = re.sub(r"[^A-Za-z0-9]+", "_", (m.group(1) if m else "Candidate")).strip("_") or "Candidate"
    role_s = re.sub(r"[^A-Za-z0-9]+", "_", role or "").strip("_") or "Role"
    dtype = {"Cover letter": "CoverLetter", "Screening": "Screening"}.get(label, "CV")
    return f"{dtype}_{name}_{role_s}"


class ScreeningIn(BaseModel):
    questions_text: str = ""        # one question per line (any ATS — paste fallback)


@app.post("/api/jobs/{job_id}/screening")
def gen_screening(job_id: str, body: ScreeningIn) -> dict:
    """Generate screening answers for a pasted list of questions (works for any ATS)."""
    job = store.get_job(job_id)
    if not job or not job.draft:
        raise HTTPException(404, "Generate a draft first.")
    qs = [{"text": ln.strip(), "type": "text", "options": []}
          for ln in body.questions_text.splitlines() if ln.strip()]
    if not qs:
        raise HTTPException(400, "Paste at least one question.")
    chosen = _suggest_app_cv(job)
    cv = store.get_app_cv_text(chosen["id"]) if chosen else store.read_base_cv()
    html = draft_mod.generate_screening(
        {"role": job.role, "company": job.company, "description": job.description}, cv, qs)
    if not html:
        raise HTTPException(502, "Couldn't generate (no API key or model error).")
    job.draft.screening_html = html
    store.save_job(job)
    return {"screening_html": html, "count": len(qs)}


@app.post("/api/jobs/{job_id}/screening/refresh")
def refresh_screening(job_id: str) -> dict:
    """Re-fetch the live questions from the job's URL and rewrite ONLY the screening
    (preserves CV/CL). Works for Lever/Greenhouse/Ashby; others say so."""
    job = store.get_job(job_id)
    if not job or not job.draft:
        raise HTTPException(404, "Generate a draft first.")
    qs = questions.fetch_questions(job.url)
    if not qs:
        raise HTTPException(400, "No questions auto-fetchable from this URL — use ↑ Screening Qs to paste them.")
    chosen = _suggest_app_cv(job)
    cv = store.get_app_cv_text(chosen["id"]) if chosen else store.read_base_cv()
    html = draft_mod.generate_screening(
        {"role": job.role, "company": job.company, "description": job.description}, cv, qs)
    if not html:
        raise HTTPException(502, "Couldn't generate (no API key or model error).")
    job.draft.screening_html = html
    store.save_job(job)
    return {"screening_html": html, "count": len(qs), "questions": [q["text"] for q in qs]}


def _resolve_real_apply(url: str) -> str:
    """Follow an aggregator link (e.g. an Adzuna post) to the REAL linked apply page,
    so ATS detection + question fetching use that page — not the aggregator. Tries a
    cheap httpx redirect, then a real browser for bot-walled aggregators (Adzuna 429s
    httpx but a browser reaches the embedded ATS / 'apply' redirect)."""
    if not url or boards_optimize.detect(url):     # already a recognised ATS URL
        return url
    import httpx
    final = url
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15, headers=questions.UA)
        if r.status_code < 400:
            final = str(r.url)
    except Exception:
        pass
    if boards_optimize.detect(final):
        return final
    try:
        rb = browser.resolve_apply(url)
        if rb and boards_optimize.detect(rb):
            return rb
    except Exception:
        pass
    return final


@app.post("/api/jobs/{job_id}/questions")
def fetch_job_questions(job_id: str) -> dict:
    """Resolve the REAL apply page, detect its ATS, and fetch + persist the live
    screening questions. This reaches past the aggregator post to the linked JD, so
    ATS optimisation is anchored on the real board — and it ALWAYS runs on Build."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.url:
        real = _resolve_real_apply(job.url)
        if real and real != job.url:
            job.url = real                         # persist the real linked apply URL
    qs = questions.fetch_questions(job.url) if job.url else []
    job.questions = qs
    store.save_job(job)
    return {"questions": [q.get("text", "") for q in qs], "count": len(qs),
            "resolved_url": job.url,
            "board": boards_optimize.ui_tip(job.url) if job.url else None}


@app.post("/api/jobs/{job_id}/resolve-apply")
def resolve_apply(job_id: str) -> dict:
    """Follow an aggregator (e.g. Adzuna) redirect to the real listing, set it as the
    job URL, and try to auto-fetch the live screening questions from it."""
    import httpx
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.url:
        raise HTTPException(400, "No URL to resolve.")
    try:
        r = httpx.get(job.url, follow_redirects=True, timeout=20, headers=questions.UA)
        final = str(r.url)
    except Exception as e:
        raise HTTPException(502, f"Couldn't reach the link ({type(e).__name__}).")
    job.url = final
    qs = questions.fetch_questions(final)
    out = {"url": final, "count": len(qs), "questions": [q["text"] for q in qs], "screening_html": ""}
    if qs and job.draft:
        chosen = _suggest_app_cv(job)
        cv = store.get_app_cv_text(chosen["id"]) if chosen else store.read_base_cv()
        html = draft_mod.generate_screening(
            {"role": job.role, "company": job.company, "description": job.description}, cv, qs)
        if html:
            job.draft.screening_html = html
            out["screening_html"] = html
    store.save_job(job)
    return out


@app.post("/api/jobs/{job_id}/research-context")
def research_context(job_id: str) -> dict:
    """Draft suggested 'About this application' notes (why-excited, cultural fit,
    emphasis, the honest gap) from the JD + company, for the user to review and edit."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    # The honest gap must be a GENUINE shortfall. Prefer the JD requirement
    # assessment (mismatch = real gap, then stretch) over the analysis "unmet"
    # list, which can mislabel a met requirement (e.g. "10+ yrs — solid match").
    unmet = []
    if job.jd and job.jd.requirements:
        unmet = [r.quote for r in job.jd.requirements if r.level == "mismatch"
                 and not fitscore.is_location_gap(r.quote)]
        unmet += [r.quote for r in job.jd.requirements if r.level == "stretch"
                  and not fitscore.is_location_gap(r.quote)]
    if not unmet:
        unmet = [u for u in ((job.analysis.unmet if job.analysis else None) or job.unmet or [])
                 if str(u).strip() and not fitscore.is_location_gap(u)]
    out = draft_mod.research_application_context(
        {"role": job.role, "company": job.company, "mode": job.mode,
         "location": job.location, "description": job.description},
        store.load_profile(), store.read_base_cv(), unmet,
        app_questions=[q.get("text", "") for q in (job.questions or []) if q.get("text")])
    # Back-fill the framing onto an existing draft that has none (older packs built
    # before research was persisted) so the Research tab stops coming up empty and
    # we don't re-run this every time the job is opened.
    if (not out.get("error") and job.draft
            and not (job.draft.ctx and (job.draft.ctx.get("angle") or "").strip())):
        job.draft.ctx = {"opener": (job.draft.ctx or {}).get("opener", "") if job.draft.ctx else "",
                         "angle": out.get("angle", ""), "why_excited": out.get("why_excited", ""),
                         "gap": out.get("gap", ""), "cultural_fit": out.get("cultural_fit", ""),
                         "emphasis": out.get("emphasis", ""), "hooks": out.get("hooks", [])}
        store.save_job(job)
    return out


@app.post("/api/jobs/{job_id}/people")
def people(job_id: str) -> dict:
    """Shortlist people to contact on LinkedIn for this role's company —
    target personas + ready-made Google/LinkedIn searches + a connection note."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    from engine import people as people_mod
    prof = store.load_profile()
    return {
        "company": job.company,
        "targets": people_mod.targets(job.company, job.role),
        "note": people_mod.connection_note(job.company, job.role, prof.get("candidate_summary", "")),
        "search_api": people_mod.has_search_api(),
    }


class CLInferIn(BaseModel):
    new_text: str = ""


class CLChange(BaseModel):
    base: str = ""
    actual: str = ""
    reason: str = ""


class CLSaveIn(BaseModel):
    new_text: str = ""
    changes: list = []


@app.post("/api/jobs/{job_id}/cl-infer")
def cl_infer(job_id: str, body: CLInferIn) -> dict:
    """Diff the pasted CL against the current draft and infer a reason per change."""
    job = store.get_job(job_id)
    if not job or not job.draft:
        raise HTTPException(404, "No draft to compare against.")
    old = clchanges.html_to_text(job.draft.cl_html)
    segs = clchanges.segment_changes(old, body.new_text)
    if not segs:
        return {"changes": []}
    reasons = clchanges.infer_reasons(segs, job.role, job.company)
    return {"changes": [{"base": o, "actual": n, "reason": r}
                        for (o, n), r in zip(segs, reasons)]}


@app.post("/api/jobs/{job_id}/cl-save")
def cl_save(job_id: str, body: CLSaveIn) -> dict:
    """Record the reviewed changes to style.md and replace the draft CL."""
    job = store.get_job(job_id)
    if not job or not job.draft:
        raise HTTPException(404, "No draft to update.")
    for c in body.changes:
        c = CLChange(**c) if isinstance(c, dict) else c
        if (c.actual or "").strip() != (c.base or "").strip() or (c.reason or "").strip():
            store.append_style_entry(job.role, job.company, c.base, c.base, c.actual, c.reason)
    job.draft.cl_html = clchanges.text_to_html(body.new_text)
    store.save_job(job)
    return {"ok": True, "cl_html": job.draft.cl_html}


def _learnings_html(md: str) -> str:
    """Render style.md (learned preferences) to the html_to_docx vocabulary."""
    import html as _html
    out = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("## "):
            out.append(f"<div class='role-h'>{_html.escape(line[3:])}</div>")
        elif line.startswith("# "):
            out.append(f"<h3>{_html.escape(line[2:])}</h3>")
        elif line.startswith("- "):
            body = _html.escape(line[2:])
            lbl = re.match(r"(AI suggested|Changed to|Reason|Context):\s*(.*)", body, re.DOTALL)
            if lbl:
                out.append(f"<p><strong>{lbl.group(1)}:</strong> {lbl.group(2)}</p>")
            else:
                out.append(f"<p>{body}</p>")
        else:
            out.append(f"<p>{_html.escape(line)}</p>")
    return "\n".join(out)


@app.get("/api/learnings.docx")
def export_learnings_docx() -> Response:
    md = store.read_style()
    if not md.strip():
        raise HTTPException(404, "No learned preferences yet.")
    data = docxcv.html_to_docx(_learnings_html(md))
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="Learned preferences.docx"'},
    )


@app.post("/api/learnings/rebuild")
def rebuild_learnings() -> dict:
    """Regroup accepted edits per application + re-distil the rule set from style.md."""
    from engine import learndistill
    return learndistill.rebuild()


@app.get("/api/learnings.md")
def export_learnings_md() -> Response:
    md = store.read_style()
    if not md.strip():
        raise HTTPException(404, "No learned preferences yet.")
    return Response(content=md, media_type="text/markdown",
                    headers={"Content-Disposition": 'attachment; filename="Learned preferences.md"'})


@app.get("/api/learnings.html", response_class=HTMLResponse)
def export_learnings_html() -> str:
    """Styled HTML of the learnings — used by the client to print a PDF."""
    md = store.read_style()
    return _learnings_html(md) if md.strip() else "<p>No learned preferences yet.</p>"


@app.post("/api/jobs/{job_id}/analysis")
def make_analysis(job_id: str) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    data = fitscore.analyze(
        {"role": job.role, "company": job.company, "location": job.location,
         "mode": job.mode, "description": job.description},
        store.load_profile(), store.read_base_cv(), score=job.score)
    job.analysis = Analysis(**data)
    # Keep the jobs-table Unmet column showing the JD gap: short tags from the
    # analysis's unmet (JD→candidate) requirements.
    gaps = [_short_tag(u) for u in (data.get("unmet") or [])
            if str(u).strip() and not fitscore.is_location_gap(u)]
    if gaps:
        job.unmet = gaps[:3]
    store.save_job(job)
    return {"analysis": job.analysis.model_dump()}


@app.post("/api/jobs/{job_id}/liveness")
def check_liveness(job_id: str) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    res = liveness.check_url(job.url)
    job.live = res["live"]
    job.live_note = res["note"]
    job.live_checked_at = fitscore._now()
    # an expired posting drops out of Active into Archived (keeps the badge);
    # a bookmark the user pinned is left alone
    if job.live == "expired" and not job.bookmarked:
        job.archived = True
    store.save_job(job)
    return {"live": job.live, "note": job.live_note,
            "checked_at": job.live_checked_at, "archived": job.archived}


def _short_tag(s: str, words: int = 6, cap: int = 42) -> str:
    """A JD requirement phrase -> a short tag for the jobs-table Unmet column."""
    s = (s or "").strip().rstrip(".")
    parts = s.split()
    out = " ".join(parts[:words])
    if len(out) > cap:
        out = out[:cap].rstrip() + "…"
    elif len(parts) > words:
        out += "…"
    return out


def _enrich_jd(job, profile: dict) -> dict:
    """Fetch the full JD and re-apply the mode/location + geo + language gates,
    persisting the job. Returns {text, archived, error}. Shared by make_jd (which then
    classifies requirements) and the batch high-scorer enrichment, so thin aggregator
    snippets get the full filtering treatment upfront."""
    import httpx

    from engine import language, textutils as _T
    text, err = job.description, ""
    if job.url:
        try:
            resp = httpx.get(job.url, follow_redirects=True, timeout=20, headers=questions.UA)
            final = str(resp.url)
            if final != job.url:
                job.url = final          # aggregator/redirect -> real apply page (e.g. Ashby)
            fetched = (fetch_mod.fetch(final) or {}).get("description", "")
            if len(fetched) > len(text):
                text = fetched
        except Exception as e:
            err = f"Could not fetch the JD page ({type(e).__name__}); showing stored text."
    if len(text) > len(job.description or ""):
        job.description = text            # keep the richer JD so geo/scoring see it
    job.jd_enriched = True
    # Correct mode/location from the full JD — thin listings (e.g. NoDesk) mislabel a
    # remote-US role as onsite/'Remote'. Re-detect the mode and surface the real region
    # in the location column (esp. US, which fails the band) -> "Remote, US".
    jd_mode = _T.detect_mode(text[:2000])
    if jd_mode:
        job.mode = jd_mode
    loc = (job.location or "").strip().lower()
    unrevealing = (not loc) or loc in ("remote", "anywhere", "worldwide", "global") or loc.startswith("remote")
    if unrevealing and _T.looks_us_only(text):
        job.location = "Remote, US" if job.mode == "remote" else "United States"
    # geo gate — hide a US-only role that an aggregator stub showed as 'Remote'. But
    # never re-archive a role the user is ALREADY archived-and-deliberately-building:
    # if they're applying anyway, let it through.
    if (pipeline.geo_excluded(job.mode, job.location, profile, text)
            and not job.bookmarked and not job.archived):
        job.archived = True
        store.save_job(job)
        return {"text": text, "archived": True, "error": err}
    # language gate — its own axis: caps the fit score + flags separately, never
    # folded into skills/domain. A C2 requirement aggregator snippets hide is caught here.
    lcfg = profile.get("languages") or {}
    la = language.assess(text, lcfg.get("spoken", ["english"]), lcfg.get("boost", []))
    if la["blocked"]:
        job.language_block = True
        job.language_note = ("Requires fluent "
                             + ", ".join(s.title() for s in la["blocking"]) + " — not spoken")
        job.score = min(job.score, int(lcfg.get("block_score_cap", 20)))
    elif job.language_block:                  # full JD clears a snippet false-positive
        job.language_block, job.language_note = False, ""
    store.save_job(job)
    return {"text": text, "archived": False, "error": err}


def enrich_high_scorers(threshold: int) -> dict:
    """Fetch full JDs + apply gates for active roles scoring >= threshold on EITHER
    scale — AI (score) or WT (weight_score). Skips already-enriched/archived roles."""
    profile = store.load_profile()
    out = {"enriched": 0, "archived": 0, "blocked": 0, "failed": 0}
    for job in store.list_jobs():
        if job.archived or job.jd_enriched or not job.url:
            continue
        if job.status not in ("new", "review"):
            continue
        if max(job.score, job.weight_score) < threshold:
            continue
        res = _enrich_jd(job, profile)
        if res["archived"]:
            out["archived"] += 1
        elif job.language_block:
            out["blocked"] += 1
        elif res["error"]:
            out["failed"] += 1
        else:
            out["enriched"] += 1
    return out


@app.post("/api/jobs/enrich")
def enrich_jobs() -> dict:
    """Manually run the high-scorer JD enrichment (same as runs after a scan)."""
    threshold = int(store.load_profile().get("jd_fetch_threshold", 70))
    return {"threshold": threshold, **enrich_high_scorers(threshold)}


@app.post("/api/jobs/{job_id}/jd")
def make_jd(job_id: str) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    profile = store.load_profile()
    res = _enrich_jd(job, profile)
    if res["archived"]:
        return {"jd": None, "geo_excluded": True,
                "note": "This role is US/Americas-based — outside your geo gate — so it's been moved to Archived."}
    text = res["text"]
    cls = fitscore.classify_requirements(text, profile, store.read_base_cv())
    reqs = [Requirement(**r) for r in cls["requirements"]]
    job.jd = JDDoc(text=text, url=job.url, fetched_at=fitscore._now(),
                   error=res["error"] or cls.get("error", ""),
                   requirements=reqs)
    # Keep the jobs-table Unmet column consistent with the JD GAP: short tags
    # built from the 'mismatch' requirements (else the scorer's tags remain).
    gaps = [_short_tag(r.quote) for r in reqs
            if r.level == "mismatch" and not fitscore.is_location_gap(r.quote)]
    if gaps:
        job.unmet = gaps[:3]
    store.save_job(job)
    return {"jd": job.jd.model_dump()}


def _cv_employers(cv_text: str) -> list:
    """The employer/role names from the CV (### headings), for the Strengthen dropdown."""
    out, seen = [], set()
    for m in re.finditer(r"^###\s+(.+)$", cv_text or "", re.M):
        name = re.split(r"\s+[—–-]\s+|\s*,\s*", m.group(1).strip())[0].strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _match_employer(raw: str, employers: list) -> str:
    """Snap an inferred employer string (e.g. 'ACME — Remote') to a CV employer."""
    r = (raw or "").strip().upper()
    for e in employers:
        if r.startswith(e.upper()):
            return e
    return (raw or "").strip()


@app.post("/api/jobs/{job_id}/strengthen")
def strengthen(job_id: str) -> dict:
    """For the match/stretch requirements, infer a CV-grounded draft point each (cached
    on the requirement). Returns them with any saved user override, for the JD tab's
    'Strengthen your match' section."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    reqs = [r for r in (job.jd.requirements if job.jd else []) if r.level in ("match", "stretch")]
    missing = [r for r in reqs if not (r.draft_point or "").strip()]
    # regenerate older mappings (made before employer-tagging) to backfill the employer
    if not missing and reqs and not any((r.draft_employer or "").strip() for r in reqs):
        missing = reqs
    if missing:
        mp = fitscore.suggest_draft_points(
            [{"quote": r.quote, "level": r.level} for r in missing],
            store.load_profile(), store.read_base_cv())
        if mp:
            for r in job.jd.requirements:
                if r.quote in mp:
                    r.draft_point = mp[r.quote]["point"]
                    r.draft_employer = mp[r.quote]["employer"]
            store.save_job(job)
    ev, cl = job.req_evidence or {}, job.req_cl or {}
    employers = _cv_employers(store.read_base_cv())
    rows = []
    for r in reqs:
        text = ev.get(r.quote) or r.draft_point
        quantified = bool(re.search(r"\d", text or ""))
        # pre-select the strongest for the cover letter: clear matches with a number
        default_cl = (r.level == "match" and quantified)
        rows.append({"quote": r.quote, "level": r.level, "draft_point": r.draft_point,
                     "employer": _match_employer(r.draft_employer, employers),
                     "evidence": ev.get(r.quote, ""), "quantified": quantified,
                     "include_cl": bool(cl.get(r.quote, default_cl))})
    return {"requirements": rows, "employers": employers}


class StatusIn(BaseModel):
    status: str
    reason: str = ""        # for skips: why — becomes a scoring anchor
    anchor: str = ""        # "up" -> promote (likes.md); else down-rank (skips.md)


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, body: StatusIn) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = body.status
    if body.status == "skipped" and (body.reason or "").strip():
        if body.anchor == "up":
            store.append_like(job.role, job.company, body.reason)   # promote: more like this
        else:
            store.append_skip(job.role, job.company, body.reason)   # down-rank similar
    store.save_job(job)
    return {"ok": True, "status": job.status}


@app.post("/api/jobs/{job_id}/learning-recap")
def learning_recap(job_id: str) -> dict:
    """After a submit: the edits this application taught (with reasons), and how the
    re-distilled global rules changed as a result."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    text = store.read_style()
    blocks = [b for b in re.split(r"(?m)^(?=## )", text) if b.startswith("## ")]
    tag = f"— {(job.role or '?')} @ {(job.company or '?')}"
    entries = []
    for b in blocks:
        head = b.splitlines()[0] if b.splitlines() else ""
        if tag not in head:
            continue
        m = re.search(r'AI suggested: "(.*?)"\n- Changed to: "(.*?)"\n- Reason: (.*)', b, re.S)
        if m:
            entries.append({"suggested": m.group(1).strip(), "changed": m.group(2).strip(),
                            "reason": m.group(3).strip()})
        else:
            entries.append({"suggested": "", "changed": "",
                            "reason": b.split("Reason:")[-1].strip()})
    before = store.read_style_rules()
    from engine import learndistill
    learndistill.rebuild_if_stale()                     # fold the new edits into the rules
    after = store.read_style_rules()
    # Compare on a normalised key so cosmetic rewording during re-distillation
    # doesn't masquerade as a brand-new global rule.
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    before_keys = {_norm(ln) for ln in before.splitlines() if ln.strip().startswith("-")}
    new_rules = [ln.strip().lstrip("-").strip()
                 for ln in after.splitlines()
                 if ln.strip().startswith("-") and _norm(ln) and _norm(ln) not in before_keys]
    summary = draft_mod.summarize_learnings(entries)
    return {"summary": summary, "entries": entries, "count": len(entries),
            "rules": after, "new_rules": new_rules}


# The files that steer drafting + scoring, exposed read-only so the user can
# review what conditions the agent (linked from the post-submit learning recap).
GUIDING_FILES = {
    "pinned":    ("Pinned rules (yours)", store.read_pinned_rules,
                  "Authoritative drafting directives you control — followed first, never auto-overwritten."),
    "rules":     ("Drafting rules (distilled)", store.read_style_rules,
                  "Condensed guidelines + guardrails applied to every draft."),
    "edits":     ("Edit log (raw accepted edits)", store.read_style,
                  "Your accepted draft edits with reasons — the source the rules are distilled from."),
    "strengths": ("Strengths (positive anchors)", store.read_strengths,
                  "Treated as MET in scoring & JD-fit, and surfaced in drafts — never flagged as gaps."),
    "likes":     ("Liked roles (up-rank anchors)", store.read_likes,
                  "Why you wanted similar roles — up-ranks them in future scoring."),
    "skips":     ("Skipped roles (down-rank anchors)", store.read_skips,
                  "Why you passed on similar roles — down-ranks them in future scoring."),
}


@app.get("/api/guiding/{name}", response_class=HTMLResponse)
def view_guiding(name: str) -> str:
    """A styled read-only view of one guiding file (opened from the learning recap)."""
    import html as _html
    item = GUIDING_FILES.get(name)
    if not item:
        raise HTTPException(404, "Unknown guiding file")
    title, reader, desc = item
    content = (reader() or "").strip()
    body = _html.escape(content) if content else "(empty — nothing recorded here yet)"
    return (f"<!doctype html><html><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{_html.escape(title)}</title><style>"
            "body{font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "max-width:760px;margin:40px auto;padding:0 20px;color:#1e2227}"
            "h1{font-size:18px;margin:0 0 4px}.desc{color:#667085;margin-bottom:16px;font-size:13px}"
            "pre{white-space:pre-wrap;background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;"
            "padding:16px;font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}</style></head>"
            f"<body><h1>{_html.escape(title)}</h1><div class=desc>{_html.escape(desc)}</div>"
            f"<pre>{body}</pre></body></html>")


class RewriteIn(BaseModel):
    text: str = ""
    instruction: str = ""
    kind: str = "cv"        # cv | cl


@app.post("/api/jobs/{job_id}/rewrite")
def rewrite_passage(job_id: str, body: RewriteIn) -> dict:
    """Re-generate one paragraph/bullet to a user prompt, during editing."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not body.instruction.strip():
        raise HTTPException(400, "Enter a rewrite instruction.")
    chosen = _suggest_app_cv(job)
    cv = store.get_app_cv_text(chosen["id"]) if chosen else store.read_base_cv()
    out = draft_mod.rewrite_text(
        {"role": job.role, "company": job.company, "description": job.description},
        cv, body.text, body.instruction, body.kind)
    if not out:
        raise HTTPException(502, "Rewrite failed (no API key or model error).")
    return {"text": out}


class JobUrlIn(BaseModel):
    url: str = ""


@app.post("/api/jobs/{job_id}/url")
def set_job_url(job_id: str, body: JobUrlIn) -> dict:
    """Edit a job's JD/application URL. Reports how many screening questions that
    URL exposes (so the user can point it at the apply page to enable auto-fetch)."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.url = (body.url or "").strip()
    store.save_job(job)
    qn = len(questions.fetch_questions(job.url)) if job.url else 0
    return {"ok": True, "url": job.url, "questions_fetched": qn}


class BookmarkIn(BaseModel):
    bookmarked: bool


@app.post("/api/jobs/{job_id}/bookmark")
def set_bookmark(job_id: str, body: BookmarkIn) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.bookmarked = body.bookmarked
    if body.bookmarked:
        job.archived = False        # bookmarking rescues a job from the archive
    store.save_job(job)
    return {"ok": True, "bookmarked": job.bookmarked}


# ---- profile / weights ---------------------------------------------------
@app.get("/api/profile")
def get_profile() -> dict:
    p = store.load_profile()
    langs = p.get("languages", {}) or {}
    return {"weights": p.get("weights", {}),
            "fit_rubric": p.get("fit_rubric", ""),   # the AI scoring rubric (primary path)
            "keywords": {                       # the lists each weight matches on
                "skills": p.get("skills", []),
                "domains": p.get("domains", []),
                "stage_signals": p.get("stage_signals", []),
            },
            "filters": {
                "geo_gate": p.get("geo_gate", []),
                "exclude_us_onsite_hybrid": bool(p.get("exclude_us_onsite_hybrid", True)),
                "recency_days": int(p.get("recency_days", 7)),
                "spoken": langs.get("spoken", []),
                "boost": langs.get("boost", []),
                "jd_fetch_threshold": int(p.get("jd_fetch_threshold", 70)),
                "shortlists": p.get("shortlists", []),
            }}


class ScoringIn(BaseModel):
    weights: dict = {}
    skills: list = []
    domains: list = []
    stage_signals: list = []


@app.post("/api/profile/scoring")
def save_scoring(body: ScoringIn) -> dict:
    """Save the weighted-factor config (weights + their keyword lists) together."""
    weights = {k: int(v) for k, v in body.weights.items()}
    total = sum(weights.values())
    if total != 100:
        raise HTTPException(400, f"Weights must sum to 100 (got {total}).")
    profile = store.load_profile()
    profile["weights"] = weights
    for field in ("skills", "domains", "stage_signals"):
        vals = [s.strip() for s in getattr(body, field) if s.strip()]
        if vals:
            profile[field] = vals
    store.save_profile(profile)
    n = pipeline.rescore_all()
    return {"ok": True, "rescored": n}


class RubricIn(BaseModel):
    rubric: str


@app.post("/api/profile/rubric")
def save_rubric(body: RubricIn) -> dict:
    rubric = body.rubric.strip()
    if len(rubric) < 30:
        raise HTTPException(400, "Rubric looks too short — describe how to score.")
    profile = store.load_profile()
    profile["fit_rubric"] = rubric
    store.save_profile(profile)
    n = pipeline.rescore_all()
    return {"ok": True, "rescored": n}


class KeywordsIn(BaseModel):
    skills: list = []
    domains: list = []
    stage_signals: list = []


@app.post("/api/profile/keywords")
def save_keywords(body: KeywordsIn) -> dict:
    profile = store.load_profile()
    for field in ("skills", "domains", "stage_signals"):
        vals = [s.strip() for s in getattr(body, field) if s.strip()]
        if vals:                                 # don't let a field be wiped to empty
            profile[field] = vals
    store.save_profile(profile)
    n = pipeline.rescore_all()                   # keyword factors + skills chips change
    return {"ok": True, "rescored": n}


@app.get("/api/doctrines")
def get_doctrines() -> dict:
    """The editable drafting-doctrine specs (cover letter, CV summary) for Settings."""
    return {"doctrines": [{"key": k, "label": m["label"], "text": draft_mod.doctrine_text(k)}
                          for k, m in draft_mod.DOCTRINES.items()]}


class DoctrineIn(BaseModel):
    text: str = ""


@app.post("/api/doctrines/{key}")
def save_doctrine(key: str, body: DoctrineIn) -> dict:
    if not draft_mod.save_doctrine_text(key, body.text):
        raise HTTPException(404, "Unknown doctrine")
    return {"ok": True}


class FiltersIn(BaseModel):
    geo_gate: list = []
    exclude_us_onsite_hybrid: bool = True
    recency_days: int = 7
    spoken: list = []
    boost: list = []
    jd_fetch_threshold: int = 70
    shortlists: Optional[list] = None   # None = leave unchanged


@app.post("/api/profile/filters")
def save_filters(body: FiltersIn) -> dict:
    profile = store.load_profile()
    profile["geo_gate"] = [g for g in body.geo_gate if g]
    profile["exclude_us_onsite_hybrid"] = bool(body.exclude_us_onsite_hybrid)
    profile["recency_days"] = max(1, min(int(body.recency_days), 90))
    langs = profile.get("languages", {}) or {}
    langs["spoken"] = [s.strip().lower() for s in body.spoken if s.strip()]
    langs["boost"] = [s.strip().lower() for s in body.boost if s.strip()]
    profile["languages"] = langs
    profile["jd_fetch_threshold"] = max(0, min(int(body.jd_fetch_threshold), 100))
    if body.shortlists is not None:
        clean = []
        for s in body.shortlists:
            sid = str(s.get("id") or s.get("label", "")).strip().lower().replace(" ", "-")
            label = str(s.get("label", "")).strip()
            match = s.get("match") if s.get("match") in ("bookmarked", "flags", "keywords") else "keywords"
            if not sid or not label:
                continue
            clean.append({"id": sid, "label": label, "icon": str(s.get("icon", "")).strip()[:3],
                          "match": match,
                          "flags": [str(x).strip() for x in (s.get("flags") or []) if str(x).strip()],
                          "keywords": [str(x).strip() for x in (s.get("keywords") or []) if str(x).strip()]})
        profile["shortlists"] = clean
    store.save_profile(profile)
    return {"ok": True}


class WeightsIn(BaseModel):
    weights: dict


@app.post("/api/profile/weights")
def save_weights(body: WeightsIn) -> dict:
    weights = {k: int(v) for k, v in body.weights.items()}
    total = sum(weights.values())
    if total != 100:
        raise HTTPException(400, f"Weights must sum to 100 (got {total}).")
    profile = store.load_profile()
    profile["weights"] = weights
    store.save_profile(profile)
    n = pipeline.rescore_all()
    return {"ok": True, "rescored": n}


# ---- base documents ------------------------------------------------------
@app.get("/api/base-docs")
def get_base_docs() -> dict:
    return {"status": store.base_status(), "files": store.base_filenames(),
            "cv": store.read_base_cv(), "cl": store.read_base_cl()}


@app.post("/api/base-docs/{kind}")
async def upload_base(kind: str, file: Optional[UploadFile] = None,
                      text: str = Form("")) -> dict:
    if kind not in ("cv", "cl"):
        raise HTTPException(404, "kind must be 'cv' or 'cl'")
    content, fname = text.strip(), ""
    if file is not None and file.filename:
        try:
            content = extract.extract_text(await file.read(), file.filename)
        except extract.ExtractError as e:
            raise HTTPException(400, str(e))
        fname = file.filename or ""
    if not content:
        raise HTTPException(400, "Nothing to save — choose a file or paste text.")
    (store.save_base_cv if kind == "cv" else store.save_base_cl)(content, fname)
    return {"ok": True, "filename": fname or "pasted text"}


@app.delete("/api/base-docs/{kind}")
def delete_base_doc(kind: str) -> dict:
    if kind not in ("cv", "cl"):
        raise HTTPException(404, "kind must be 'cv' or 'cl'")
    (store.delete_base_cv if kind == "cv" else store.delete_base_cl)()
    return {"ok": True}


# ---- CV library: matching CV (scoring) + application variants (drafting) --
import uuid as _uuid


@app.get("/api/cvs")
def get_cvs() -> dict:
    return {
        "matching": {"uploaded": store.base_status()["cv"],
                     "filename": store.base_filenames()["cv"],
                     "chars": len(store.read_base_cv())},
        "applications": store.list_app_cvs(),
        "reference_pdf": store.reference_pdf_name(),
    }


@app.post("/api/cvs/app")
async def save_app_cv(id: str = Form(""), name: str = Form(...), flags: str = Form(""),
                      file: Optional[UploadFile] = None, text: str = Form("")) -> dict:
    content, fname = text.strip(), ""
    if file is not None and file.filename:
        try:
            content = extract.extract_text(await file.read(), file.filename)
        except extract.ExtractError as e:
            raise HTTPException(400, str(e))
        fname = file.filename or ""
    if not content:
        raise HTTPException(400, "Nothing to save — choose a file or paste text.")
    if not name.strip():
        raise HTTPException(400, "Give the variant a name.")
    cv_id = id.strip() or _uuid.uuid4().hex[:8]
    flag_list = [f.strip() for f in flags.split(",") if f.strip()]
    store.save_app_cv(cv_id, name.strip(), flag_list, content, fname)
    return {"ok": True, "id": cv_id}


@app.delete("/api/cvs/app/{cv_id}")
def delete_app_cv(cv_id: str) -> dict:
    store.delete_app_cv(cv_id)
    return {"ok": True}


@app.post("/api/cvs/reference")
async def upload_reference_pdf(file: UploadFile) -> dict:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a PDF.")
    store.save_reference_pdf(await file.read(), file.filename)
    return {"ok": True, "filename": file.filename}


@app.get("/api/cvs/reference")
def get_reference_pdf() -> FileResponse:
    if not store.reference_pdf_name():
        raise HTTPException(404, "No reference PDF uploaded.")
    return FileResponse(store.REFERENCE_PDF_PATH, media_type="application/pdf",
                        filename=store.reference_pdf_name())


def _suggest_app_cv(job):
    """Pick the application CV whose target flags best overlap the job's flags.
    Domain match is decisive: a web3/crypto role picks the Web3-tagged CV."""
    cvs = store.list_app_cvs()
    if not cvs:
        return None
    jf = set(job.flags or [])
    for domain in ("web3",):                    # domain-specific CV wins for its domain
        if domain in jf:
            dcvs = [c for c in cvs if domain in (c.get("flags") or [])]
            if dcvs:
                return max(dcvs, key=lambda c: len(jf & set(c.get("flags", []))))
    return max(cvs, key=lambda c: len(jf & set(c.get("flags", []))))


def _board_id(board: dict) -> str:
    return board.get("name", "").lower().replace(" ", "")


def _depth_mode(board: dict, fetcher) -> str:
    """How fetch depth works for this board — drives which page knobs the UI shows.
    paginated = page_size + pages | pages = pages only (site-fixed page size) |
    all = every role in one call | feed = single feed/window | render = one render
    per query."""
    if board.get("provider") and board.get("slug"):
        return "all"                                 # ATS: all open roles at once
    if getattr(fetcher, "MANUAL_ONLY", False):
        return "render"                              # slow render-per-query built-in
    declared = getattr(fetcher, "DEPTH", None)       # adapter knows best (e.g. Supabase API)
    if declared:
        return declared
    tier = board.get("tier")
    if tier == "browser":
        return "render"                              # one rendered page per query
    if tier == "listing":
        return "pages"                               # paginates; page size fixed by site
    return "feed"                                    # single feed within the window


def _resolve_fetcher(board_id: str):
    """Static board module, or a generated fetcher for a custom ATS / listing /
    browser board."""
    f = BOARD_FETCHERS.get(board_id)
    if f:
        return f
    for b in store.load_boards():
        if _board_id(b) != board_id:
            continue
        if b.get("provider") and b.get("slug"):
            return ats.board_fetcher(b["provider"], b["slug"])
        if b.get("tier") == "getro" and b.get("url"):
            return getro.board_fetcher(b["url"], b.get("name", ""))
        if b.get("tier") == "listing" and b.get("url"):
            return listing.board_fetcher(b["url"], b.get("search_template", ""),
                                         b.get("search_sep", "%20"))
        if b.get("tier") == "browser" and b.get("url"):
            return browser.board_fetcher(b["url"], b.get("search_template", ""),
                                         b.get("search_sep", "%20"))
    return None


@app.get("/api/boards")
def get_boards() -> dict:
    profile = store.load_profile()
    boards = store.load_boards()
    recency = int(profile.get("recency_days", 7))
    for b in boards:
        b["id"] = _board_id(b)
        b["recency_days"] = recency
        fetcher = BOARD_FETCHERS.get(b["id"])
        custom_fetch = bool((b.get("provider") and b.get("slug"))
                            or (b.get("tier") in ("listing", "browser") and b.get("url")))
        b["fetchable"] = fetcher is not None or custom_fetch
        b["manual_scan"] = (bool(getattr(fetcher, "MANUAL_ONLY", False))
                            or (b.get("tier") == "browser" and bool(b.get("custom"))))
        b["query_based"] = (bool(getattr(fetcher, "QUERY_BASED", False)) if fetcher
                            else bool(b.get("search_template")))
        if b["query_based"]:
            b["queries"] = store.role_queries_for(profile, b["id"])
        # visibility for Settings: the URL/template we fetch, the per-board horizon
        # the NEXT scan will use, and the effective page depth
        b["fetch_url"] = b.get("search_template") or b.get("url") or b.get("endpoint", "")
        b["next_since"] = pipeline.board_since(b["id"], profile)
        b["last_scanned"] = store.get_board_scan(b["id"])
        b["depth"] = _depth_mode(b, fetcher)         # which page knobs actually apply
        b.setdefault("page_size", DEFAULT_PAGE_SIZE)
        b.setdefault("pages", DEFAULT_PAGES.get(b.get("tier"), 3))
    return {"boards": boards}


class BoardSettingsIn(BaseModel):
    page_size: int = 0
    pages: int = 0


@app.post("/api/boards/{board_id}/settings")
def save_board_settings(board_id: str, body: BoardSettingsIn) -> dict:
    boards = store.load_boards()
    hit = None
    for b in boards:
        if _board_id(b) == board_id:
            hit = b
            if body.page_size:
                b["page_size"] = min(max(body.page_size, 1), 100)
            if body.pages:
                b["pages"] = min(max(body.pages, 1), 25)
    if hit is None:
        raise HTTPException(404, "Board not found.")
    store.save_boards(boards)
    return {"ok": True, "page_size": hit.get("page_size"), "pages": hit.get("pages")}


def _classify_board(url: str, name: str = None, tier: str = "") -> dict:
    """Build a custom-board dict for `url`. tier '' auto-detects (ATS → static listing
    → browser); pass 'ats'|'listing'|'browser' to force how it's fetched."""
    host = urlparse(url).netloc.replace("www.", "")
    host_name = host.split(".")[0].title() or "Custom board"
    profile = store.load_profile()
    queries = store.role_queries_for(profile, "default") or []
    info = ats.parse_careers_url(url)

    def _ats():
        if not info:
            raise HTTPException(400, "That URL isn't a recognised ATS (Greenhouse / Lever / Ashby / …).")
        nm = name or info["slug"].replace("-", " ").replace("_", " ").title()
        return {"name": nm, "tier": "ats", "region": "global", "url": url,
                "provider": info["provider"], "slug": info["slug"], "auth": "none",
                "enabled": True, "custom": True,
                "note": f"{info['provider'].title()} board for {nm} — scans all roles, filtered to your target roles."}

    def _listing():
        t, sep = browser.make_search_template(url, queries)
        return {"name": name or host_name, "tier": "listing", "region": "", "url": url,
                "search_template": t, "search_sep": sep, "auth": "none",
                "enabled": True, "custom": True,
                "note": "Listing board — fast static fetch" + (", fans out over your role queries." if t else " of this URL.")}

    def _browser():
        t, sep = browser.make_search_template(url, queries)
        return {"name": name or host_name, "tier": "browser", "region": "", "url": url,
                "search_template": t, "search_sep": sep, "auth": "none",
                "enabled": True, "custom": True,
                "note": ("Browser tier — renders the page with Playwright (rate-limited), "
                         + ("fans out over your role queries." if t else "scrapes this one URL.")
                         + " Won't defeat heavy bot walls (e.g. Glassdoor).")}

    tier = (tier or "").lower()
    if tier == "ats":
        return _ats()
    if tier == "listing":
        return _listing()
    if tier == "browser":
        return _browser()
    if info:                       # auto: a scannable ATS company board
        return _ats()
    if listing.probe(url) > 0:     # auto: server-rendered static HTML (fast)
        return _listing()
    return _browser()              # auto: JS-rendered / bot-light


class BoardAddIn(BaseModel):
    url: str


@app.post("/api/boards")
def add_board(body: BoardAddIn) -> dict:
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "Paste a board or careers URL.")
    if not urlparse(url).scheme:
        url = "https://" + url
    boards = store.load_boards()
    ids = {_board_id(b) for b in boards}
    board = _classify_board(url)
    bid = _board_id(board)
    if bid in ids:
        raise HTTPException(409, f"A board named “{board['name']}” already exists.")
    boards.append(board)
    store.save_boards(boards)
    board["id"] = bid
    board["fetchable"] = board["tier"] in ("ats", "listing", "browser")
    return {"board": board}


@app.delete("/api/boards/{board_id}")
def delete_board(board_id: str) -> dict:
    boards = store.load_boards()
    keep = [b for b in boards if not (_board_id(b) == board_id and b.get("custom"))]
    if len(keep) == len(boards):
        raise HTTPException(404, "No custom board with that id (built-in boards can't be removed).")
    store.save_boards(keep)
    return {"ok": True, "removed": board_id}


class BoardUrlIn(BaseModel):
    url: str
    tier: str = ""        # ""=auto-detect | ats | listing | browser


@app.post("/api/boards/{board_id}/url")
def update_board_url(board_id: str, body: BoardUrlIn) -> dict:
    """Change the fetch URL (and optionally force the fetch tier) of a board you added.
    Re-detects how to fetch it; keeps the board's name (so its id stays stable) and
    your depth settings. Built-in boards can't be edited."""
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "Enter a URL.")
    if not urlparse(url).scheme:
        url = "https://" + url
    boards = store.load_boards()
    i = next((k for k, b in enumerate(boards) if _board_id(b) == board_id), None)
    if i is None:
        raise HTTPException(404, "Board not found.")
    if not boards[i].get("custom"):
        raise HTTPException(400, "Only boards you added can have their URL changed.")
    old = boards[i]
    new = _classify_board(url, name=old.get("name"), tier=body.tier)   # keep name -> stable id
    for k in ("page_size", "pages", "enabled", "last_scanned"):        # preserve user settings
        if old.get(k) is not None:
            new[k] = old[k]
    boards[i] = new
    store.save_boards(boards)
    out = dict(new)
    out["id"] = _board_id(new)
    out["fetchable"] = new["tier"] in ("ats", "listing", "browser")
    return {"board": out}


@app.get("/api/role-queries")
def get_role_queries() -> dict:
    profile = store.load_profile()
    rq = store._role_queries_raw(profile)
    query_boards = [{"id": _board_id(b), "name": b.get("name", "")}
                    for b in store.load_boards()
                    if getattr(BOARD_FETCHERS.get(_board_id(b)), "QUERY_BASED", False)]
    return {"default": rq.get("default", []),
            "overrides": {qb["id"]: rq.get(qb["id"]) for qb in query_boards},
            "query_boards": query_boards}


class RoleQueriesIn(BaseModel):
    default: list
    overrides: dict = {}


@app.post("/api/role-queries")
def save_role_queries(body: RoleQueriesIn) -> dict:
    if not [q for q in body.default if q.strip()]:
        raise HTTPException(400, "The default query list can't be empty.")
    store.save_role_queries(body.default, body.overrides)
    return {"ok": True}


# Per-board fetch depth. Full-feed boards ignore these (a single feed bounded by
# `since`); paginated/templated boards use them as the page budget per query.
DEFAULT_PAGE_SIZE = 50
DEFAULT_PAGES = {"listing": 5, "browser": 5, "ats": 1}


def _board_depth(board_id: str, body) -> tuple:
    """Effective (page_size, pages): explicit request > saved board setting >
    tier default. Capped for safety."""
    b = next((x for x in store.load_boards() if _board_id(x) == board_id), {})
    ps = body.page_size or int(b.get("page_size") or DEFAULT_PAGE_SIZE)
    pg = body.pages or int(b.get("pages") or DEFAULT_PAGES.get(b.get("tier"), 3))
    return min(max(ps, 1), 100), min(max(pg, 1), 25)


class BoardFetchIn(BaseModel):
    keyword: str = ""
    remote_only: bool = False
    page_size: int = 0          # 0 -> use the board's saved/default depth
    pages: int = 0


@app.post("/api/boards/{board_id}/preview")
def board_preview(board_id: str, body: BoardFetchIn) -> dict:
    fetcher = _resolve_fetcher(board_id)
    if not fetcher:
        raise HTTPException(404, "No fetch tool for this board yet.")
    profile = store.load_profile()
    since = pipeline.freshness_since(profile)        # Preview always shows the full 7-day window
    ps, pg = _board_depth(board_id, body)
    try:
        res = pipeline.board_fetch(fetcher, profile, page_size=ps, pages=pg,
                                   remote_only=body.remote_only,
                                   keyword=body.keyword, board_id=board_id, since=since)
    except Exception as e:
        raise HTTPException(502, f"Board fetch failed: {e}")
    kept, dropped = pipeline.filter_target_roles(res["jobs"], profile)
    kept, stale = pipeline.drop_stale(kept, profile, since=since)
    # skip roles already in the tracker so we don't re-score doubles
    kept, already = pipeline.split_new(kept)
    results = pipeline.score_raws(kept, profile)   # infers location where blank
    rows = []
    for raw, sr in zip(kept, results):
        if pipeline.geo_excluded(raw.get("mode"), raw.get("location"), profile, raw.get("description", "")):
            dropped += 1                            # US/Americas-only — not relevant
            continue
        rows.append({
            "role": raw["role"], "company": raw["company"], "mode": raw["mode"],
            "url": raw["url"], "posted": raw.get("posted", ""),
            "score": sr.score, "reason": sr.reason,
            "language_block": sr.language_block, "language_note": sr.language_note,
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"total": res["total"], "pages_fetched": res["pages_fetched"],
            "page_size": res["page_size"], "direct_url": res["direct_url"],
            "queries": res.get("queries", []), "off_target": dropped,
            "stale": stale, "already": already, "since": since,
            "next_since": pipeline.board_since(board_id, profile), "jobs": rows}


@app.post("/api/boards/{board_id}/import")
def board_import(board_id: str, body: BoardFetchIn) -> dict:
    from datetime import date
    fetcher = _resolve_fetcher(board_id)
    if not fetcher:
        raise HTTPException(404, "No fetch tool for this board yet.")
    profile = store.load_profile()
    since = pipeline.board_since(board_id, profile)  # first import 7d, then incremental
    ps, pg = _board_depth(board_id, body)
    try:
        res = pipeline.board_fetch(fetcher, profile, page_size=ps, pages=pg,
                                   remote_only=body.remote_only,
                                   keyword=body.keyword, board_id=board_id, since=since)
    except Exception as e:
        raise HTTPException(502, f"Board fetch failed: {e}")
    theirstack.record_fetched(res["jobs"])           # advance incremental watermark (no-op for other boards)
    imported, skipped, dropped, stale, good = pipeline.import_raws(res["jobs"], since=since)
    store.set_board_scan(board_id, date.today().isoformat())   # stamp this board's scan
    return {"imported": imported, "skipped": skipped, "dropped": dropped,
            "stale": stale, "good": good, "total": res["total"], "since": since}


@app.get("/api/applications.csv")
def download_csv() -> FileResponse:
    store.rewrite_csv()
    return FileResponse(store.CSV_PATH, media_type="text/csv", filename="applications.csv")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
