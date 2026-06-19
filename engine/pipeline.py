"""Single-JD pipeline: URL/text -> fetch -> score -> persisted Job."""
from __future__ import annotations

from adapters import dedupe, url_key

from . import fetch as fetch_mod
from . import fitscore
from . import founderfit
from . import rolematch
from . import voiceai
from . import score as score_mod
from . import store
from . import textutils
from .models import Job, ScoreResult


def score_raws(raws: list, profile: dict) -> list:
    """Score raws -> [ScoreResult]. Keyword score gives the factor breakdown +
    fallback; the LLM fit score (CV + LinkedIn) overrides score/reason when
    available — except language-blocked jobs keep their capped score."""
    results = [score_mod.score_job(r, profile) for r in raws]
    fits = fitscore.score_batch(raws, profile, store.read_base_cv())
    if fits:
        for raw, res, fit in zip(raws, results, fits):
            if not fit:
                continue
            res.drivers = fit.get("drivers", [])
            res.unmet = fit.get("unmet", [])
            # infer location only when the board didn't give one
            if not (raw.get("location") or "").strip() and fit.get("location"):
                raw["location"] = fit["location"]
            if not res.language_block:        # blocked jobs keep their capped score
                res.score = fit["score"]
                res.reason = fit["reason"] or res.reason
    return results


def _build_job(raw: dict, result: ScoreResult, role: dict, status: str) -> Job:
    return Job(
        id=store.new_id(), date=store.today(),
        role=raw["role"], company=raw["company"], url=raw.get("url", ""),
        mode=raw["mode"], location=raw.get("location", ""),
        salary=raw.get("salary", ""), posted=raw.get("posted", ""),
        description=raw["description"], status=status,
        score=result.score, weight_score=sum(f.points for f in result.factors),
        reason=result.reason, factors=result.factors,
        drivers=result.drivers, unmet=result.unmet,
        language_block=result.language_block, language_note=result.language_note,
        role_off_target=not role["match"], role_note=role["note"],
        source=raw.get("source", ""),
        remote_anywhere=(raw.get("mode") == "remote" and (
            textutils.is_fully_remote(raw.get("description", ""))
            or (raw.get("location", "") or "").strip().lower() in ("", "remote"))),
        flags=_job_flags(raw.get("title") or raw.get("role", ""), raw.get("description", "")),
    )


def _job_flags(title: str, description: str) -> list:
    flags = founderfit.detect(title, description)
    if voiceai.detect(title, description):
        flags.append("voice_ai")
    from . import web3flag
    if web3flag.detect(title, description):
        flags.append("web3")
    return flags


def drop_stale(raws: list, profile: dict, since: str = None) -> tuple:
    """Split raws into (fresh, stale_count). With `since` (YYYY-MM-DD), drop
    postings dated before it; otherwise use the recency_days window. A raw with
    no known posting date is treated as fresh (we can't prove it's old)."""
    days = int(profile.get("recency_days", 7))
    fresh, stale = [], 0
    for r in raws:
        posted = (r.get("posted", "") or "")[:10]
        if since:
            too_old = bool(posted) and posted < since
        else:
            age = store.days_since(posted) if posted else None
            too_old = age is not None and age > days
        if too_old:
            stale += 1
        else:
            fresh.append(r)
    return fresh, stale


def _age_days(job: Job):
    """Job age: posting date if known, else the date it was added to the tracker."""
    if job.posted:
        a = store.days_since(job.posted)
        if a is not None:
            return a
    return store.days_since(job.date)


ATS_HOSTS = ("greenhouse", "lever.co", "ashbyhq", "recruitee", "smartrecruiters",
             "workable", "personio")


def is_ats_job(job) -> bool:
    """A direct-employer ATS listing = a CURRENTLY-OPEN role; it shouldn't age out
    by posting date (only by being pulled from the board)."""
    from urllib.parse import urlparse
    host = urlparse(getattr(job, "url", "") or "").netloc.lower()
    return any(h in host for h in ATS_HOSTS)


def archive_stale(profile: dict = None) -> int:
    """Archive non-bookmarked, un-applied jobs older than the recency window.
    ATS company listings are exempt — they're open roles, not dated postings."""
    profile = profile or store.load_profile()
    days = int(profile.get("recency_days", 7))
    archived = 0
    for j in store.list_jobs():
        if (j.archived or j.bookmarked or is_ats_job(j)
                or j.status in ("applied", "approved", "skipped")):
            continue
        age = _age_days(j)
        if age is not None and age > days:
            j.archived = True
            store.save_job(j)
            archived += 1
    return archived


def _supports_since(fetcher) -> bool:
    import inspect
    try:
        return "since" in inspect.signature(fetcher.fetch).parameters
    except (TypeError, ValueError):
        return False


def freshness_since(profile: dict = None) -> str:
    """The recency horizon: today minus recency_days. Everything newer than this
    is 'fresh' (not aged out). Used for manual Preview/Import so you see ALL
    qualifying roles still in window, not just the delta since the last scan."""
    from datetime import date, timedelta
    profile = profile or store.load_profile()
    return (date.today() - timedelta(days=int(profile.get("recency_days", 7)))).isoformat()


def scan_since(profile: dict = None) -> str:
    """The incremental horizon for the auto-Refresh: 1 day before the last fetch
    (so we only pull what's new), or the recency window on the first ever run."""
    from datetime import date, timedelta
    profile = profile or store.load_profile()
    last = store.get_last_fetch()
    if last:
        return (date.fromisoformat(last) - timedelta(days=1)).isoformat()
    return freshness_since(profile)


def board_since(board_id: str, profile: dict = None) -> str:
    """Per-board horizon: a board's FIRST scan gets the full recency window (so a
    newly-added board pulls its 7-day backlog); after that it's incremental — only
    what's new since that board's own last scan."""
    from datetime import date, timedelta
    profile = profile or store.load_profile()
    last = store.get_board_scan(board_id)
    if last:
        return (date.fromisoformat(last) - timedelta(days=1)).isoformat()
    return freshness_since(profile)


def board_fetch(fetcher, profile: dict, page_size: int = 20, pages: int = 1,
                remote_only: bool = False, keyword: str = "", board_id: str = None,
                since: str = None) -> dict:
    """Fetch a board with the right strategy, returning merged + deduped raw jobs.

    - explicit `keyword` -> single search/filter (manual override)
    - query-based board (e.g. Adzuna) -> fan out over profile['role_queries'],
      one search per phrase, merged
    - feed board -> single fetch; the role gate downstream enforces target roles
    Returns {jobs, total, pages_fetched, page_size, direct_url, queries}.
    """
    if keyword.strip():
        res = fetcher.fetch(keyword=keyword.strip(), remote_only=remote_only,
                            page_size=page_size, pages=pages)
        res["queries"] = [keyword.strip()]
        return res

    if getattr(fetcher, "QUERY_BASED", False):
        queries = store.role_queries_for(profile, board_id) or ["product manager"]
        skw = {"since": since} if (since and _supports_since(fetcher)) else {}
        merged, fetched, direct = [], 0, ""
        for q in queries:
            r = fetcher.fetch(keyword=q, remote_only=remote_only,
                              page_size=page_size, pages=pages, **skw)
            merged.extend(r["jobs"])
            fetched += r.get("pages_fetched", 0)
            direct = direct or r.get("direct_url", "")
        jobs = dedupe(merged)
        return {"jobs": jobs, "total": len(jobs), "pages_fetched": fetched,
                "page_size": page_size, "direct_url": direct, "queries": queries}

    kw = {"since": since} if (since and _supports_since(fetcher)) else {}
    res = fetcher.fetch(keyword="", remote_only=remote_only,
                        page_size=page_size, pages=pages, **kw)
    res["queries"] = []
    return res


def role_assess(raw: dict, profile: dict) -> dict:
    """Is this raw job a target role? Checks the title (falls back to role)."""
    role_text = raw.get("title") or raw.get("role") or ""
    return rolematch.assess(role_text, profile.get("roles", []),
                            profile.get("roles_exclude", []))


def geo_excluded(mode: str, location: str, profile: dict, description: str = "") -> bool:
    """Geo-exclude any US / Americas location — including US-remote and even
    multi-region roles that merely list the US (per the user's 'no US' rule).
    When the location field is unrevealing ('Remote'/blank), fall back to the JD
    body so a US-listed role that hid its country still gets dropped."""
    if not profile.get("exclude_us_onsite_hybrid"):
        return False
    from .textutils import is_americas, is_far_geo, is_remote_friendly, is_us, looks_us_only
    if is_us(location) or is_americas(location):
        return True
    # on-site/hybrid in a far region (India/MENA/APAC) is a hard no — no relocation
    if mode in ("onsite", "hybrid") and is_far_geo(location):
        return True
    # location gives no usable region -> consult the JD text (catches US roles stored
    # as just 'Remote', e.g. aggregator stubs). Region-tagged remote is left alone.
    loc = (location or "").strip().lower()
    unrevealing = (loc in ("", "remote", "anywhere", "worldwide", "global")
                   or loc.startswith("remote")) and not is_remote_friendly(location)
    if unrevealing and description and looks_us_only(description):
        return True
    return False


def filter_target_roles(raws: list, profile: dict) -> tuple:
    """Split raws into (on-target, off-target-count) — used for board fetches."""
    kept, dropped = [], 0
    for raw in raws:
        if role_assess(raw, profile)["match"]:
            kept.append(raw)
        else:
            dropped += 1
    return kept, dropped


def make_job(raw: dict, status: str = "review") -> Job:
    """Score a normalized raw job dict, flag off-target roles, persist, return it."""
    profile = store.load_profile()
    result = score_raws([raw], profile)[0]
    role = role_assess(raw, profile)
    job = _build_job(raw, result, role, status)
    store.save_job(job)
    return job


def add_from_paste(url: str = "", text: str = "") -> Job:
    """Fetch (or use pasted text), score, persist, and return the Job."""
    raw = fetch_mod.fetch_or_paste(url=url, text=text)
    return make_job(raw, status="review")


def _sig(company: str, title: str) -> str:
    return (company or "").strip().lower() + "|" + (title or "").strip().lower()


def split_new(raws: list, existing: list = None) -> tuple:
    """Split raws into (not-yet-stored, already-stored-count), deduped against the
    tracker by URL path AND company|title — so doubles are dropped BEFORE the
    expensive LLM scoring, on both Preview and Import."""
    existing = existing if existing is not None else store.list_jobs()
    seen_urls = {url_key(j.url) for j in existing if j.url}
    seen_sigs = {_sig(j.company, j.role) for j in existing}
    new, already = [], 0
    for r in raws:
        u = url_key(r.get("url", ""))
        sig = _sig(r.get("company", ""), r.get("title") or r.get("role", ""))
        if (u and u in seen_urls) or sig in seen_sigs:
            already += 1
            continue
        new.append(r)
        if u:
            seen_urls.add(u)
        seen_sigs.add(sig)
    return new, already


def import_raws(raws: list, since: str = None) -> tuple:
    """Save new on-target jobs from raw dicts.

    Dedupes against stored jobs by BOTH URL path and content signature
    (company|title) — so the SAME role coming from a second board (e.g. Adzuna
    after Product Builder Jobs) is skipped, keeping the version imported first.
    Off-target roles dropped; postings older than the window skipped.
    Returns (imported, skipped, dropped, stale, good_match) where good_match is
    the number of imported roles scoring > 75.
    """
    from . import liveness
    profile = store.load_profile()
    kept, dropped = filter_target_roles(dedupe(raws), profile)
    before = len(kept)
    kept = [r for r in kept if not geo_excluded(r.get("mode"), r.get("location"), profile, r.get("description", ""))]
    dropped += before - len(kept)                  # US hybrid/onsite filtered out
    before = len(kept)
    kept = [r for r in kept if not liveness.looks_dead(r.get("description", ""))]
    dropped += before - len(kept)                  # filled / expired postings dropped
    kept, stale = drop_stale(kept, profile, since=since)

    fresh, skipped = split_new(kept)               # drop doubles before scoring

    # scoring infers a location for jobs the board left blank — re-apply the geo
    # gate now so US/Americas-only remote roles are dropped, never stored
    results = score_raws(fresh, profile)
    imported = good = 0
    for raw, result in zip(fresh, results):
        if geo_excluded(raw.get("mode"), raw.get("location"), profile, raw.get("description", "")):
            dropped += 1
            continue
        role = role_assess(raw, profile)
        store.save_job(_build_job(raw, result, role, "new"))
        imported += 1
        if result.score > 75:
            good += 1
    return imported, skipped, dropped, stale, good


def rescore_all() -> int:
    """Re-score every stored job with the current profile (keyword + LLM fit)."""
    profile = store.load_profile()
    jobs = store.list_jobs()
    raws = [{"role": j.role, "title": j.role, "company": j.company,
             "mode": j.mode, "location": j.location,
             "description": j.description} for j in jobs]
    results = score_raws(raws, profile)
    kept = 0
    for job, raw, result in zip(jobs, raws, results):
        if not (job.location or "").strip() and raw.get("location"):
            job.location = raw["location"]      # inferred from the JD by the fit model
        # US/Americas-only remote (or US onsite/hybrid) isn't relevant — delete it
        if geo_excluded(job.mode, job.location, profile, job.description):
            store.delete_job(job.id)
            continue
        job.score = result.score
        job.weight_score = sum(f.points for f in result.factors)
        job.reason = result.reason
        job.factors = result.factors
        job.drivers = result.drivers
        job.unmet = result.unmet
        job.language_block = result.language_block
        job.language_note = result.language_note
        role = role_assess({"title": job.role, "role": job.role}, profile)
        job.role_off_target = not role["match"]
        job.role_note = role["note"]
        job.flags = _job_flags(job.role, job.description)
        job.remote_anywhere = (job.mode == "remote" and (
            textutils.is_fully_remote(job.description)
            or (job.location or "").strip().lower() in ("", "remote")))
        store.save_job(job)
        kept += 1
    return kept
