"""Fetch the REAL application/screening questions from a job's ATS apply page.

Used at draft time so the Screening section answers the actual questions instead
of guessing. All fetching is static HTTP (no browser): Lever embeds the form as
JSON in the apply page; Greenhouse exposes questions via its boards API. Unknown
ATSes return [] and the drafter falls back to inferring likely questions.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import urlparse

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
# standard fields we never need to answer as "questions"
_SKIP = {"first name", "last name", "full name", "name", "email", "phone", "resume",
         "resume/cv", "cv", "cover letter", "linkedin profile", "linkedin", "github",
         "website", "portfolio", "location (city)", "how did you hear about us?"}


def fetch_questions(url: str) -> list:
    """[{text, type, required, options:[...]}] of custom application questions, or []."""
    if not url:
        return []
    host = urlparse(url).netloc.lower()
    try:
        if "lever.co" in host:
            return _lever(url)
        if "greenhouse" in host:
            return _greenhouse(url)
        if "ashbyhq.com" in host:
            return _ashby(url)
    except Exception:
        return []
    return []


def _balanced_json(s: str, start: int) -> str:
    """Extract a brace-balanced JSON object from s starting at index `start` ('{')."""
    depth = 0
    instr = False
    esc = False
    out = []
    for ch in s[start:]:
        out.append(ch)
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            instr = not instr
            continue
        if instr:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
    return "".join(out)


def _ashby(url: str) -> list:
    m = re.search(r"ashbyhq\.com/([^/?]+)/([0-9a-fA-F-]{8,})", url)
    if not m:
        return []
    org, jid = m.group(1), m.group(2)
    html = _get(f"https://jobs.ashbyhq.com/{org}/{jid}/application")
    key = html.find("window.__appData")
    if key == -1:
        return []
    brace = html.find("{", key)
    try:
        data = json.loads(_balanced_json(html, brace))
    except Exception:
        return []
    out, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            title = (o.get("title") or o.get("humanReadablePath") or "").strip()
            path = str(o.get("path", ""))
            ftype = o.get("fieldType") or o.get("type")
            is_field = "isNullable" in o or "isRequired" in o or "fieldType" in o
            if title and is_field and not path.startswith("_systemfield"):
                tl = title.lower()
                if tl not in seen and not any(s in tl for s in _SKIP):
                    seen.add(tl)
                    opts = []
                    for k in ("selectableValues", "options", "values"):
                        if isinstance(o.get(k), list):
                            opts = [(x.get("label") or x.get("value") or x.get("title"))
                                    for x in o[k] if isinstance(x, dict)]
                            opts = [x for x in opts if x]
                            if opts:
                                break
                    out.append({"text": title, "type": ftype or "text",
                                "required": bool(o.get("isRequired") or not o.get("isNullable", True)),
                                "options": opts})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return out


def _get(url: str) -> str:
    return httpx.get(url, timeout=20, follow_redirects=True, headers=UA).text


def _lever(url: str) -> list:
    # normalize to the apply page
    base = re.sub(r"/apply/?$", "", url.split("?")[0]).rstrip("/")
    htmltext = _get(base + "/apply")
    out = []
    for m in re.finditer(r'value="([^"]*)"\s+name="cards\[[^\]]+\]\[baseTemplate\]"', htmltext):
        try:
            card = json.loads(html.unescape(m.group(1)))
        except Exception:
            continue
        for f in card.get("fields", []):
            text = (f.get("text") or "").strip()
            if text and text.lower() not in _SKIP:
                out.append({"text": text, "type": f.get("type", "text"),
                            "required": bool(f.get("required")),
                            "options": [o.get("text") for o in f.get("options", []) if o.get("text")]})
    return out


def _greenhouse(url: str) -> list:
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url) or \
        re.search(r"greenhouse\.io/embed/job_app\?for=([^&]+).*?gh_jid=(\d+)", url)
    if not m:
        return []
    token, jid = m.group(1), m.group(2)
    data = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}",
                     params={"questions": "true"}, timeout=20, headers=UA).json()
    out = []
    for q in data.get("questions", []):
        label = re.sub(r"<[^>]+>", "", q.get("label", "")).strip()
        if not label or label.lower() in _SKIP:
            continue
        fields = q.get("fields", [])
        ftype = fields[0].get("type") if fields else "text"
        opts = [v.get("label") for f in fields for v in (f.get("values") or []) if v.get("label")]
        out.append({"text": label, "type": ftype, "required": bool(q.get("required")), "options": opts})
    return out


def format_for_prompt(questions: list, limit: int = 12) -> str:
    """Numbered block for the drafting prompt."""
    lines = []
    for i, q in enumerate(questions[:limit], 1):
        tag = q.get("type", "text")
        opts = q.get("options") or []
        line = f"{i}. [{tag}] {q['text']}"
        if opts:
            line += "  | options: " + " | ".join(opts)
        lines.append(line)
    return "\n".join(lines)
