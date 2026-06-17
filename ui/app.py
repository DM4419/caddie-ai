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

from adapters import (adzuna, ats, browser, cryptocurrencyjobs, listing,
                      productbuilderjobs, remoteok, web3career, workingnomads)
from engine import clchanges
from engine import draft as draft_mod
from engine import docxcv
from engine import extract
from engine import fetch as fetch_mod
from engine import fitscore, liveness, pipeline, questions, score as score_mod, store
from engine.models import Analysis, JDDoc, Requirement

# Board id -> fetcher module (boards that support bulk preview/import).
BOARD_FETCHERS = {
    "productbuilderjobs": productbuilderjobs,
    "remoteok": remoteok,
    "workingnomads": workingnomads,
    "web3career": web3career,
    "adzuna": adzuna,
    "cryptocurrencyjobs": cryptocurrencyjobs,
}

app = FastAPI(title="caddie-ai")
STATIC = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def _startup() -> None:
    store.ensure_dirs()
    store.rewrite_csv()


@app.middleware("http")
async def _no_cache(request, call_next):
    """Force revalidation of the UI/static assets so edits show up immediately."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text())


# ---- jobs ----------------------------------------------------------------
@app.get("/api/jobs")
def get_jobs(archived: bool = False) -> dict:
    base = store.base_status()
    all_jobs = store.list_jobs()
    jobs = [j.model_dump(exclude={"description", "factors", "draft"})
            for j in all_jobs if bool(j.archived) == archived]
    archived_count = sum(1 for j in all_jobs if j.archived)
    active = [j for j in all_jobs if not j.archived]
    FOUNDER = {"eir", "zero_to_one", "founder_welcome"}
    untriaged = lambda j: j.status not in ("applied", "skipped")
    counts = {
        "applied": sum(1 for j in active if j.status == "applied"),
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
            "counts": counts, "sources": sources}


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
            imp, sk, dr, st, gd = pipeline.import_raws(res["jobs"], since=since)
            imported += imp; skipped += sk; dropped += dr; stale += st; good += gd
            store.set_board_scan(bid, today)         # stamp this board's scan
            _set_board(i, status="done", imported=imp)
        except Exception as e:
            _set_board(i, status="error", error=f"{type(e).__name__}: {e}"[:160])
    archived = pipeline.archive_stale(profile)
    store.set_last_fetch(today)
    with _refresh_lock:
        _refresh_state.update(running=False, done=True, totals={
            "imported": imported, "skipped": skipped, "dropped": dropped,
            "stale": stale, "archived": archived, "good": good})


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
    return {"job": job.model_dump(),
            "cv_options": [{"id": c["id"], "name": c["name"]} for c in cvs]}


@app.get("/api/style")
def get_style() -> dict:
    import re
    text = store.read_style()
    parts = re.split(r"(?m)^(?=## )", text)
    preamble = parts[0] if parts and not parts[0].startswith("## ") else ""
    entries = [p.rstrip() for p in parts if p.startswith("## ")]
    return {"preamble": preamble, "entries": entries}


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


@app.post("/api/jobs/{job_id}/override")
def save_override(job_id: str, body: OverrideIn) -> dict:
    """Record a manual CV edit + rationale into style.md so future drafts learn
    it. Never edits the base CV or this draft's other applications."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    store.append_style_entry(job.role, job.company, body.base, body.suggested,
                             body.actual, body.reason)
    return {"ok": True}


class DraftIn(BaseModel):
    cv_id: str = ""
    opener: str = ""          # ""=auto by flags | standard | cheeky
    why_excited: str = ""     # recent trigger / what pushed you to apply
    gap: str = ""             # the honest JD gap to name
    cultural_fit: str = ""    # one true working-style match point
    emphasis: str = ""        # free-text: JD themes to accentuate in the letter


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
    real_qs = questions.fetch_questions(job.url)        # actual ATS application questions
    draft = draft_mod.draft_documents(
        {"role": job.role, "company": job.company, "mode": job.mode,
         "description": job.description},
        base_cv, store.read_base_cl(),
        app_ctx={"opener": opener,
                 "why_excited": body.why_excited if body else "",
                 "gap": body.gap if body else "",
                 "cultural_fit": body.cultural_fit if body else "",
                 "emphasis": body.emphasis if body else ""},
        questions=real_qs,
        role_fit={"matched": job.drivers, "unmet": job.unmet},
    )
    draft.cv_used = ({"id": chosen["id"], "name": chosen["name"]} if chosen
                     else {"id": "", "name": "Matching CV (no variants yet)"})
    job.draft = draft
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
    unmet = (job.analysis.unmet if job.analysis else None) or job.unmet or []
    return draft_mod.research_application_context(
        {"role": job.role, "company": job.company, "mode": job.mode,
         "location": job.location, "description": job.description},
        store.load_profile(), store.read_base_cv(), unmet)


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


@app.post("/api/jobs/{job_id}/jd")
def make_jd(job_id: str) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    # fetch the full JD from the apply URL; fall back to the stored description
    text, err = job.description, ""
    if job.url:
        import httpx
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
    res = fitscore.classify_requirements(text, store.load_profile(), store.read_base_cv())
    reqs = [Requirement(**r) for r in res["requirements"]]
    job.jd = JDDoc(text=text, url=job.url, fetched_at=fitscore._now(),
                   error=err or res.get("error", ""),
                   requirements=reqs)
    # Keep the jobs-table Unmet column consistent with the JD GAP: short tags
    # built from the 'mismatch' requirements (else the scorer's tags remain).
    gaps = [_short_tag(r.quote) for r in reqs
            if r.level == "mismatch" and not fitscore.is_location_gap(r.quote)]
    if gaps:
        job.unmet = gaps[:3]
    store.save_job(job)
    return {"jd": job.jd.model_dump()}


class StatusIn(BaseModel):
    status: str
    reason: str = ""        # for skips: why — becomes a negative scoring anchor


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, body: StatusIn) -> dict:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = body.status
    if body.status == "skipped" and (body.reason or "").strip():
        store.append_skip(job.role, job.company, body.reason)
    store.save_job(job)
    return {"ok": True, "status": job.status}


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


class FiltersIn(BaseModel):
    geo_gate: list = []
    exclude_us_onsite_hybrid: bool = True
    recency_days: int = 7
    spoken: list = []


@app.post("/api/profile/filters")
def save_filters(body: FiltersIn) -> dict:
    profile = store.load_profile()
    profile["geo_gate"] = [g for g in body.geo_gate if g]
    profile["exclude_us_onsite_hybrid"] = bool(body.exclude_us_onsite_hybrid)
    profile["recency_days"] = max(1, min(int(body.recency_days), 90))
    langs = profile.get("languages", {}) or {}
    langs["spoken"] = [s.strip().lower() for s in body.spoken if s.strip()]
    profile["languages"] = langs
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

    host = urlparse(url).netloc.replace("www.", "")
    host_name = host.split(".")[0].title() or "Custom board"
    profile = store.load_profile()
    queries = store.role_queries_for(profile, "default") or []
    info = ats.parse_careers_url(url)
    if info:                                   # a scannable ATS company board
        name = info["slug"].replace("-", " ").replace("_", " ").title()
        board = {"name": name, "tier": "ats", "region": "global", "url": url,
                 "provider": info["provider"], "slug": info["slug"],
                 "auth": "none", "enabled": True, "custom": True,
                 "note": f"{info['provider'].title()} board for {name} — scans all roles, filtered to your target roles."}
    elif listing.probe(url) > 0:               # server-rendered static HTML (fast)
        template, sep = browser.make_search_template(url, queries)
        board = {"name": host_name, "tier": "listing", "region": "", "url": url,
                 "search_template": template, "search_sep": sep,
                 "auth": "none", "enabled": True, "custom": True,
                 "note": "Listing board — fast static fetch"
                         + (", fans out over your role queries." if template else " of this URL.")}
    else:                                      # JS-rendered/bot-light -> browser tier
        template, sep = browser.make_search_template(url, queries)
        board = {"name": host_name, "tier": "browser", "region": "", "url": url,
                 "search_template": template, "search_sep": sep, "auth": "none",
                 "enabled": True, "custom": True,
                 "note": ("Browser tier — renders the search with Playwright (rate-limited), "
                          + ("fans out over your role queries." if template
                             else "scrapes this one URL.")
                          + " Won't defeat heavy bot walls (e.g. Glassdoor).")}

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
        if pipeline.geo_excluded(raw.get("mode"), raw.get("location"), profile):
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
    imported, skipped, dropped, stale, good = pipeline.import_raws(res["jobs"], since=since)
    store.set_board_scan(board_id, date.today().isoformat())   # stamp this board's scan
    return {"imported": imported, "skipped": skipped, "dropped": dropped,
            "stale": stale, "good": good, "total": res["total"], "since": since}


@app.get("/api/applications.csv")
def download_csv() -> FileResponse:
    store.rewrite_csv()
    return FileResponse(store.CSV_PATH, media_type="text/csv", filename="applications.csv")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
