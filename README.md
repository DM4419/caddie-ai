# caddie-ai

**A local-first AI agent for a product manager's job hunt.** It fetches a job, scores
how well it fits *you*, and drafts a tailored CV + cover letter + screening answers —
then gets out of the way so you review, edit, and submit every application yourself.

> Like a golf caddie: it reads the course, hands you the right club, and tells you the
> line — but you take every swing. **caddie-ai never auto-submits anything.**

---

## Why

Applying well is slow: read the JD, judge whether it's worth your time, then rewrite your
CV and cover letter to match — for every single role. caddie-ai automates the *judgement
and the first draft*, where an LLM genuinely helps, and keeps the human firmly in the loop
for everything that carries risk (what you send, where you send it).

It runs entirely on your machine. Your CV, your application history, and your API key never
leave it.

## What it does

1. **Fetch** — paste a job URL (or point it at a board) and it pulls a clean, normalised job description.
2. **Score** — every role gets an **AI fit score (0–100)** judged against your CV and a plain-English rubric you control, plus a transparent **weighted score** (remote / skills / domain / stage) as a cross-check and no-AI fallback.
3. **Draft** — for roles worth it, it tailors your base CV + cover letter and answers screening questions, recording each change as *base text + tailored text + rationale* so you can see exactly what it changed and why.
4. **Review & submit** — you compare, edit, download (PDF/DOCX), and apply manually. Applications are tracked in a local CSV.

## Architecture

A small, dependency-light Python codebase — plain modules over frameworks.

```
adapters/   board fetchers, organised by reliability tier:
              api      — public JSON feeds & no-key ATSs (Greenhouse/Lever/Ashby/…)
              listing  — fast static HTML
              browser  — Playwright, rate-limited, indexes only matching rows
engine/     fetch (URL→JD) · score + fitscore (weighted + AI fit) · draft (Anthropic API)
            · pipeline (single JD end-to-end) · store (one normalised Job shape) · models
ui/         FastAPI backend + a static single-page UI
cv/         base-cv.md, base-cl.md  (your source documents)
data/       boards.yaml, profile.yaml (criteria + weights), applications.csv (tracker)
docs/       UX design specs (v1→v4)
```

**Design principles**
- **One normalised `Job` shape** across every adapter, validated with `pydantic`.
- **Tiered fetching** — build and trust the cheap, robust API tier before the fragile browser tier; the browser tier applies the board's *own* filters and never crawls full sites.
- **Scoring weights live in config**, never hardcoded — edit `profile.yaml` (or the Settings UI) and everything re-scores.
- **Auditable drafts** — every tailored change keeps its provenance for a side-by-side compare view.

## Safety & privacy by design

- **No auto-submit, ever.** The agent drafts and fills; you submit. (Avoids ToS/ban risk.)
- **Human-in-the-loop.** Nothing is finalised without your review.
- **Local-first.** Runs on `localhost`; no web host, no telemetry.
- **Secrets stay in `.env`** (gitignored), never in code. The repo ships only `.env.example`.

## Quick start

```bash
git clone <your-fork-url> caddie-ai && cd caddie-ai
cp .env.example .env          # then add your Anthropic API key
./run.sh                      # first run sets up venv + deps, then opens the app
```

`run.sh` creates the virtualenv, installs dependencies (and the Playwright browser),
and opens **http://127.0.0.1:8000**. Get an Anthropic API key at
https://console.anthropic.com. Full setup, customization, and troubleshooting are in
**[SETUP.md](SETUP.md)**.

> The repo ships a **sample** CV, cover-letter backbone, and profile so it runs out of the
> box. Replace them with your own in the **Settings** tab (or edit `cv/` and
> `data/profile.yaml`) to make scoring and drafting meaningful.

## Tech stack

Python 3.11+ · FastAPI · pydantic · httpx · BeautifulSoup · Playwright · the Anthropic API.

## Status

A working personal project, built phase by phase (see `claude-code-runbook.md` and
`CLAUDE.md` for the build plan and conventions). Shared as a portfolio piece — the public
copy contains sample data only; no real application history or credentials.

## License

[MIT](LICENSE)
