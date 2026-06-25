"""Local file storage: profile, base docs, per-job JSON, and the CSV tracker.

Source of truth = one JSON file per job under data/jobs/. The CSV at
data/applications.csv is a derived tracker/export, rewritten on every change so
it always matches the JSON and stays a valid download.
"""
from __future__ import annotations

import csv
import json
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .models import Job

BASE_DIR = Path(__file__).resolve().parents[1]
DATA = BASE_DIR / "data"
CV_DIR = BASE_DIR / "cv"
JOBS_DIR = DATA / "jobs"
DRAFTS_DIR = DATA / "drafts"
CSV_PATH = DATA / "applications.csv"
PROFILE_PATH = DATA / "profile.yaml"
BOARDS_PATH = DATA / "boards.yaml"
STATE_PATH = DATA / "state.json"
BASE_CV_PATH = CV_DIR / "base-cv.md"      # the "matching" CV (drives scoring)
BASE_CL_PATH = CV_DIR / "base-cl.md"
APPCV_DIR = CV_DIR / "apps"               # application CV variants (ATS text)
REFERENCE_PDF_PATH = CV_DIR / "reference.pdf"   # one global human-layout reference

CSV_FIELDS = ["id", "date", "score", "role", "company", "url", "mode", "status", "reason"]


def ensure_dirs() -> None:
    for d in (DATA, CV_DIR, JOBS_DIR, DRAFTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---- profile -------------------------------------------------------------
def load_profile() -> dict:
    with PROFILE_PATH.open() as f:
        return yaml.safe_load(f)


def save_profile(profile: dict) -> None:
    with PROFILE_PATH.open("w") as f:
        yaml.safe_dump(profile, f, sort_keys=False, allow_unicode=True)


def load_boards() -> list:
    if not BOARDS_PATH.exists():
        return []
    with BOARDS_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data.get("boards", [])


_BOARDS_HEADER = (
    "# Job boards. tier: api | browser | ats | manual\n"
    "# 'ats' boards carry {provider, slug} and are scanned by adapters/ats.py.\n"
    "# Boards added via the UI are marked custom: true.\n\n"
)


def save_boards(boards: list) -> None:
    """Persist the board registry (strips per-request 'id'/'fetchable' fields)."""
    ensure_dirs()
    clean = []
    for b in boards:
        clean.append({k: v for k, v in b.items()
                      if k not in ("id", "fetchable", "query_based", "queries")})
    with BOARDS_PATH.open("w") as f:
        f.write(_BOARDS_HEADER)
        yaml.safe_dump({"boards": clean}, f, sort_keys=False, allow_unicode=True)


def _role_queries_raw(profile: dict) -> dict:
    """Normalize role_queries to {default: [...], <board_id>: [...]} form."""
    rq = profile.get("role_queries")
    if isinstance(rq, list):          # legacy flat list = the default
        return {"default": rq}
    if isinstance(rq, dict):
        return rq
    return {"default": []}


def role_queries_for(profile: dict, board_id: str = None) -> list:
    """Effective query list for a board: its override, else the default."""
    rq = _role_queries_raw(profile)
    if board_id and rq.get(board_id):
        return rq[board_id]
    return rq.get("default", [])


def save_role_queries(default: list, overrides: dict) -> None:
    """Persist {default + non-empty per-board overrides} to profile.yaml."""
    profile = load_profile()
    rq = {"default": [q.strip() for q in default if q.strip()]}
    for bid, lst in (overrides or {}).items():
        cleaned = [q.strip() for q in (lst or []) if q.strip()]
        if cleaned:
            rq[bid] = cleaned
    profile["role_queries"] = rq
    save_profile(profile)


# ---- base documents ------------------------------------------------------
def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"base_cv_uploaded": False, "base_cl_uploaded": False}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def base_status() -> Dict[str, bool]:
    s = _load_state()
    return {"cv": bool(s.get("base_cv_uploaded")), "cl": bool(s.get("base_cl_uploaded"))}


def get_last_fetch() -> str:
    return _load_state().get("last_fetch", "")


def set_last_fetch(date_str: str) -> None:
    state = _load_state()
    state["last_fetch"] = date_str
    _save_state(state)


def get_board_scan(board_id: str) -> str:
    """ISO date this board was last scanned (""=never -> a first scan gets the
    full recency window; later scans only fetch what's new since)."""
    return _load_state().get("board_scans", {}).get(board_id, "")


def set_board_scan(board_id: str, date_str: str) -> None:
    state = _load_state()
    state.setdefault("board_scans", {})[board_id] = date_str
    _save_state(state)


def read_base_cv() -> str:
    return BASE_CV_PATH.read_text() if BASE_CV_PATH.exists() else ""


def read_base_cl() -> str:
    return BASE_CL_PATH.read_text() if BASE_CL_PATH.exists() else ""


def save_base_cv(text: str, filename: str = "") -> None:
    BASE_CV_PATH.write_text(text)
    state = _load_state()
    state["base_cv_uploaded"] = True
    state["base_cv_filename"] = filename or "pasted text"
    _save_state(state)


def save_base_cl(text: str, filename: str = "") -> None:
    BASE_CL_PATH.write_text(text)
    state = _load_state()
    state["base_cl_uploaded"] = True
    state["base_cl_filename"] = filename or "pasted text"
    _save_state(state)


def delete_base_cv() -> None:
    if BASE_CV_PATH.exists():
        BASE_CV_PATH.unlink()
    state = _load_state()
    state["base_cv_uploaded"] = False
    state.pop("base_cv_filename", None)
    _save_state(state)


def delete_base_cl() -> None:
    if BASE_CL_PATH.exists():
        BASE_CL_PATH.unlink()
    state = _load_state()
    state["base_cl_uploaded"] = False
    state.pop("base_cl_filename", None)
    _save_state(state)


def base_filenames() -> dict:
    s = _load_state()
    return {"cv": s.get("base_cv_filename", ""), "cl": s.get("base_cl_filename", "")}


# ---- application CV library (variants used to draft applications) ---------
def list_app_cvs() -> list:
    """[{id, name, flags, filename, chars}] — metadata for each application CV."""
    out = []
    for c in _load_state().get("app_cvs", []):
        p = APPCV_DIR / f"{c['id']}.md"
        out.append({**c, "chars": len(p.read_text()) if p.exists() else 0})
    return out


def get_app_cv_text(cv_id: str) -> str:
    p = APPCV_DIR / f"{cv_id}.md"
    return p.read_text() if p.exists() else ""


def save_app_cv(cv_id: str, name: str, flags: list, text: str, filename: str = "") -> None:
    APPCV_DIR.mkdir(parents=True, exist_ok=True)
    (APPCV_DIR / f"{cv_id}.md").write_text(text)
    state = _load_state()
    cvs = [c for c in state.get("app_cvs", []) if c["id"] != cv_id]
    cvs.append({"id": cv_id, "name": name, "flags": flags, "filename": filename or "pasted text"})
    state["app_cvs"] = cvs
    _save_state(state)


def delete_app_cv(cv_id: str) -> None:
    p = APPCV_DIR / f"{cv_id}.md"
    if p.exists():
        p.unlink()
    state = _load_state()
    state["app_cvs"] = [c for c in state.get("app_cvs", []) if c["id"] != cv_id]
    _save_state(state)


def save_reference_pdf(data: bytes, filename: str) -> None:
    CV_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE_PDF_PATH.write_bytes(data)
    state = _load_state()
    state["reference_pdf_filename"] = filename
    _save_state(state)


def reference_pdf_name() -> str:
    return _load_state().get("reference_pdf_filename", "") if REFERENCE_PDF_PATH.exists() else ""


# ---- style.md: learned edit preferences (feeds future drafts) -------------
STYLE_PATH = DATA / "style.md"


def read_style() -> str:
    return STYLE_PATH.read_text() if STYLE_PATH.exists() else ""


def read_style_recent(cap: int = 40000) -> str:
    """Learnings for the model, capped. Entries are appended newest-last, so when
    the file exceeds the cap we keep the MOST RECENT entries (recent edits reflect
    current preference), trimmed to a clean entry boundary."""
    md = read_style()
    if len(md) <= cap:
        return md
    tail = md[-cap:]
    cut = tail.find("\n## ")
    if cut != -1:
        tail = tail[cut + 1:]
    return "# Learned drafting preferences (most recent — older entries trimmed)\n\n" + tail


# ---- distilled layers over style.md (raw log) ---------------------------
# style.md       = raw append-only log of every captured edit (source of truth)
# style-rules.md = distilled, deduped, generalised do/don'ts (the rule set)
# style-examples.md = accepted before/after, grouped per application
STYLE_RULES_PATH = DATA / "style-rules.md"
STYLE_EXAMPLES_PATH = DATA / "style-examples.md"


def read_style_rules() -> str:
    return STYLE_RULES_PATH.read_text() if STYLE_RULES_PATH.exists() else ""


def write_style_rules(text: str) -> None:
    ensure_dirs()
    STYLE_RULES_PATH.write_text(text)


# ---- style-pinned.md: user-authored drafting directives that are AUTHORITATIVE and
#      never auto-overwritten (the distiller regenerates style-rules.md, not this) ----
PINNED_PATH = DATA / "style-pinned.md"


def read_pinned_rules() -> str:
    return PINNED_PATH.read_text() if PINNED_PATH.exists() else ""


def read_pinned_bullets() -> list:
    """Just the rule lines (drop the file header/comment)."""
    return [ln.strip()[1:].strip() for ln in read_pinned_rules().splitlines()
            if ln.strip().startswith("- ")]


def append_pinned_rule(text: str) -> None:
    """Add a durable, authoritative drafting rule (e.g. a correction to a mis-learned
    preference). Survives re-distillation; the drafter follows it first."""
    if not (text or "").strip():
        return
    ensure_dirs()
    if not PINNED_PATH.exists():
        PINNED_PATH.write_text(
            "# Pinned drafting rules (yours — authoritative, never auto-overwritten)\n\n"
            "Hand-edit freely. These take priority over the auto-distilled rules and "
            "constrain what the distiller may infer.\n")
    with PINNED_PATH.open("a") as f:
        f.write(f"- {text.strip()}\n")


def write_style_examples(text: str) -> None:
    ensure_dirs()
    STYLE_EXAMPLES_PATH.write_text(text)


def _style_blocks(md: str) -> list:
    """Parse style.md into [(company, block_text)] entries."""
    import re as _re
    out = []
    for blk in md.split("\n## ")[1:]:
        head = blk.splitlines()[0]
        m = _re.search(r"@\s*(.+)$", head)
        company = (m.group(1).strip() if m else "?")
        out.append((company, "## " + blk.rstrip()))
    return out


def read_style_examples_balanced(per_company: int = 2, cap: int = 6000) -> str:
    """Most-recent `per_company` accepted examples PER application, so no single
    company (e.g. Mistral) dominates the drafting context."""
    src = STYLE_EXAMPLES_PATH if STYLE_EXAMPLES_PATH.exists() else STYLE_PATH
    md = src.read_text() if src.exists() else ""
    if not md:
        return ""
    by_co = {}
    for company, block in _style_blocks(md):
        by_co.setdefault(company, []).append(block)
    picked = []
    for company, blocks in by_co.items():
        picked.extend(blocks[-per_company:])     # most recent per company
    text = "\n\n".join(picked)
    return text[:cap]


def read_style_recent_entries(n: int = 3) -> str:
    """The newest n raw entries — keeps brand-new edits influencing drafts before
    the rules/examples are rebuilt."""
    blocks = [b for _, b in _style_blocks(read_style())]
    return "\n\n".join(blocks[-n:])


# ---- skips.md: why the user passed on roles (negative scoring anchors) ----
SKIPS_PATH = DATA / "skips.md"


def read_skips() -> str:
    return SKIPS_PATH.read_text() if SKIPS_PATH.exists() else ""


def read_skips_recent(cap: int = 8000) -> str:
    md = read_skips()
    if len(md) <= cap:
        return md
    tail = md[-cap:]
    cut = tail.find("\n## ")
    return tail[cut + 1:] if cut != -1 else tail


# ---- strengths.md: positive anchors (things to treat as MET, never as gaps) ----
STRENGTHS_PATH = DATA / "strengths.md"


def read_strengths() -> str:
    return STRENGTHS_PATH.read_text() if STRENGTHS_PATH.exists() else ""


def append_strength(text: str) -> None:
    if not (text or "").strip():
        return
    ensure_dirs()
    if not STRENGTHS_PATH.exists():
        STRENGTHS_PATH.write_text("# Candidate strengths (positive anchors)\n\n"
                                  "Things to treat as MET — never flag as unmet/gaps — and to surface in "
                                  "scoring, analysis, and tailoring.\n")
    with STRENGTHS_PATH.open("a") as f:
        f.write(f"- {text.strip()}\n")


def append_skip(role: str, company: str, reason: str) -> None:
    """Record why a role was skipped — fed to scoring to down-rank similar roles."""
    if not (reason or "").strip():
        return
    ensure_dirs()
    if not SKIPS_PATH.exists():
        SKIPS_PATH.write_text("# Skipped-role reasons (negative scoring anchors)\n\n"
                              "Why the user passed on roles. Fed to AI fit scoring to down-rank "
                              "roles that share the same off-putting traits.\n")
    with SKIPS_PATH.open("a") as f:
        f.write(f"\n## {today()} — {role or '?'} @ {company or '?'}\n"
                f"- {(reason or '').strip()[:600]}\n")


# ---- likes.md: positive ROLE anchors — "more roles like this" (Skip + Train ↑) ----
LIKES_PATH = DATA / "likes.md"


def read_likes() -> str:
    return LIKES_PATH.read_text() if LIKES_PATH.exists() else ""


def read_likes_recent(cap: int = 8000) -> str:
    md = read_likes()
    if len(md) <= cap:
        return md
    tail = md[-cap:]
    cut = tail.find("\n## ")
    return tail[cut + 1:] if cut != -1 else tail


def append_like(role: str, company: str, reason: str) -> None:
    """Record a role the user skipped but WANTS MORE OF — fed to scoring to up-rank similar roles."""
    if not (reason or "").strip():
        return
    ensure_dirs()
    if not LIKES_PATH.exists():
        LIKES_PATH.write_text("# Wanted-role reasons (positive scoring anchors)\n\n"
                              "Roles the user skipped but wants MORE of. Fed to AI fit scoring to "
                              "up-rank roles that share the same attractive traits.\n")
    with LIKES_PATH.open("a") as f:
        f.write(f"\n## {today()} — {role or '?'} @ {company or '?'}\n"
                f"- {(reason or '').strip()[:600]}\n")


def write_style(text: str) -> None:
    ensure_dirs()
    STYLE_PATH.write_text(text)


def append_style_entry(role: str, company: str, base: str, suggested: str,
                       actual: str, reason: str) -> None:
    """Record a manual CV edit so future drafts learn the user's preference.
    Never touches the base CV — only this user-controlled log."""
    ensure_dirs()
    if not STYLE_PATH.exists():
        STYLE_PATH.write_text("# Learned drafting preferences\n\n"
                              "From the user's manual edits in the review compare modal. "
                              "Applied as guidance to future drafts.\n")
    block = (f"\n## {today()} — {role or '?'} @ {company or '?'}\n"
             f"- Context: {role} at {company}\n"
             f"- AI suggested: \"{(suggested or '').strip()[:2000]}\"\n"
             f"- Changed to: \"{(actual or '').strip()[:2000]}\"\n"
             f"- Reason: {(reason or '').strip()[:1500]}\n")
    with STYLE_PATH.open("a") as f:
        f.write(block)


# ---- jobs ----------------------------------------------------------------
def new_id() -> str:
    return uuid.uuid4().hex[:8]


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def save_job(job: Job) -> None:
    ensure_dirs()
    # atomic write: a concurrent reader never sees a half-written (invalid) file —
    # it gets the old complete file or the new one, never a partial truncated one.
    p = _job_path(job.id)
    tmp = p.parent / (p.name + ".tmp")
    tmp.write_text(job.model_dump_json(indent=2))
    os.replace(tmp, p)
    rewrite_csv()


def delete_job(job_id: str) -> None:
    p = _job_path(job_id)
    if p.exists():
        p.unlink()
    rewrite_csv()


def get_job(job_id: str) -> Optional[Job]:
    p = _job_path(job_id)
    if not p.exists():
        return None
    return Job.model_validate_json(p.read_text())


def list_jobs() -> List[Job]:
    ensure_dirs()
    # resilient: skip a transiently-partial or corrupt file rather than 500 the WHOLE
    # list (one bad file used to make the entire app look down).
    jobs = []
    for p in JOBS_DIR.glob("*.json"):
        try:
            jobs.append(Job.model_validate_json(p.read_text()))
        except Exception:
            continue
    # rank by score desc, then most recent date
    jobs.sort(key=lambda j: (j.score, j.date), reverse=True)
    return jobs


def rewrite_csv() -> None:
    """Regenerate the CSV tracker from all job JSON files."""
    ensure_dirs()
    jobs = list_jobs()
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for j in jobs:
            w.writerow({k: getattr(j, k) for k in CSV_FIELDS})


def today() -> str:
    return date.today().isoformat()


def days_since(date_str: str):
    """Whole days from a YYYY-MM-DD string to today, or None if unparseable."""
    try:
        y, m, d = (int(x) for x in str(date_str)[:10].split("-"))
        return (date.today() - date(y, m, d)).days
    except Exception:
        return None
