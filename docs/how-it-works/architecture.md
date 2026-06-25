# Architecture

A small, dependency-light Python codebase — plain modules over frameworks.

```
adapters/   board fetchers, organised by reliability tier:
              api      — public JSON feeds & no-key ATSs (Greenhouse/Lever/Ashby/…)
              listing  — fast static HTML
              browser  — Playwright, rate-limited, indexes only matching rows
            ats.py — detect provider/slug from a careers URL
engine/     fetch (URL→JD) · score + fitscore (weighted + AI fit, requirement check)
            · draft (research + tailoring + provenance) · learndistill (the learning loop)
            · boards_optimize (per-ATS draft tuning) · people (outreach) · questions (screening)
            · clchanges (bulk CL diff→reasons) · pipeline · store (one Job shape) · models (pydantic)
            cvbuilder/ — self-contained conversational CV builder (models · engine · render · store)
ui/         FastAPI backend + a static single-page UI (+ /cv-builder page)
cv/         base-cv.md, base-cl.md  (your source documents)
data/       boards.yaml · profile.yaml (criteria + weights) · applications.csv (tracker)
            · style*.md / skips.md / likes.md / strengths.md (the learning layer)
docs/       how-it-works/ module docs · UX design specs (v1→v4)
```

## Design principles

- **One normalised `Job` shape** across every adapter, validated with `pydantic` — scoring and
  drafting are decoupled from where a role came from.
- **Tiered fetching** — build and trust the cheap, robust API tier before the fragile browser
  tier; the browser tier applies the board's *own* filters and never crawls full sites.
- **Config over code** — scoring weights, rubric, filters, and board queries live in
  `profile.yaml` / `boards.yaml` (and the Settings UI), never hardcoded; editing them re-scores
  everything.
- **LLM produces data, not layout** — drafts and the CV builder return structured/marked data;
  deterministic code renders the final document.
- **Auditable everything** — drafts keep change provenance; the learning layer keeps the raw log
  separate from the distilled rules so you can always see *why* the tool behaves as it does.
- **Human-in-the-loop by construction** — the loop is closed by *your* review, never auto-submit.
