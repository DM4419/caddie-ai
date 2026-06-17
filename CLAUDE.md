# CLAUDE.md — caddie-ai

## What this is
A locally-run Python agent that helps me apply to product-management roles. It scores job descriptions, drafts a tailored CV + cover letter + screening answers from my base documents, and lets me review and edit before I apply manually. Runs on my Mac; no web host.

## Build order (follow `claude-code-runbook.md`)
- **Phase 0 (ship first):** paste a job URL → fetch → score → draft over single base CV/CL → review → download PDF. No boards.
- Then: config + boards → API-tier fetch → score + CSV → drafting → full UI → browser-assisted tier (last).
- Later only: 3-base CV router, learning loop, scheduling.
- Build the easy (API) tier fully before the fragile (browser) tier. Stop and eyeball `applications.csv` before expanding.

## Architecture
- `adapters/` — board fetchers by tier: `api` (Greenhouse/Lever/Ashby/Adzuna/RSS), `browser` (Playwright, filtered, index matches only), `manual` (by hand).
- `engine/` — `fetch.py` (URL → JD), `score.py` (weights from profile.yaml), `draft.py` (Anthropic API), `scan.py` (batch), `pipeline.py` (single JD).
- `ui/` — FastAPI + static UI matching `docs/ux-spec-v4.html`.
- `cv/` — `base-cv.md`, `base-cl.md` (single set for MVP).
- `data/` — `boards.yaml`, `profile.yaml` (criteria + `weights` summing to 100), `applications.csv` (tracker), `drafts/`.

## Commands
- Activate env: `source .venv/bin/activate`
- Run UI: `uvicorn ui.app:app --reload --port 8000`
- Run scan: `python -m engine.scan`

## Conventions
- Python 3.11+, type hints, `pydantic` models for Job/Score/Draft.
- One normalized `Job` shape across all adapters.
- Scoring weights live in `profile.yaml`, never hardcoded. Factors (weights): full remote 30, skills & qualifications match 30, domain match 30, stage 10. Domain-match targets: PropTech, B2B SaaS, B2B2C, platforms, crypto/web3.
- Every drafted change is recorded as: base text + custom text + rationale (for the review UI's compare modal).
- Keep functions small; prefer plain modules over frameworks.

## Hard rules
- **No auto-submit.** The agent drafts and fills, but I submit every application myself. ToS/ban risk.
- **Human-in-the-loop.** Nothing is sent or finalised without my review.
- **Secrets:** API key in `.env` (gitignored), never in code. Board logins (if ever) via macOS Keychain (`keyring`), never plaintext.
- **Fetching stays in Python**, not Zapier. Zapier is only ever optional, later, for notifications.
- **Overrides never auto-edit the base CV** or other applications — they only influence future drafts via `data/style.md`, which I control.
- **Browser tier never crawls full sites** — apply the board's own filters, index only matching rows.
- Single base CV/CL for v1; do not build the 3-base router until the core loop is proven.

## Profile (for scoring + tailoring)
The candidate's criteria live in `data/profile.yaml` (a sample is shipped with the
repo — replace it with your own, or edit it in the UI's Settings tab). It holds:
- `geo_gate` — the hard work-mode/region filter; rows outside it are dropped before storing.
  Full remote scores the full remote weight; hybrid passes the gate but scores partial.
- `candidate_summary` + `fit_rubric` — what the AI scores and ranks roles against.
- Target domains, target roles, and stage signals used by the weighted score.
- B2B/B2C is an emphasis axis at draft time, not a separate CV.
