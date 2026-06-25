"""Persist CV-builder sessions as JSON, isolated from the job store."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .models import BuilderSession

BASE_DIR = Path(__file__).resolve().parents[2]
SESSIONS_DIR = BASE_DIR / "data" / "cvbuilder"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def new_session() -> BuilderSession:
    s = BuilderSession(id=uuid.uuid4().hex[:8], created=_now())
    save(s)
    return s


def get(session_id: str) -> Optional[BuilderSession]:
    p = _path(session_id)
    if not p.exists():
        return None
    try:
        return BuilderSession.model_validate_json(p.read_text())
    except Exception:          # schema drift / corrupt -> treat as gone (a new one starts)
        return None


def save(session: BuilderSession) -> None:
    ensure_dirs()
    _path(session.id).write_text(session.model_dump_json(indent=2))


def list_sessions() -> List[BuilderSession]:
    ensure_dirs()
    out = []
    for p in SESSIONS_DIR.glob("*.json"):
        try:
            out.append(BuilderSession.model_validate_json(p.read_text()))
        except Exception:
            continue
    return sorted(out, key=lambda s: s.created, reverse=True)
