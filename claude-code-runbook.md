# Job Applicator — Claude Code Build Runbook

Spartan step-by-step. Assumes macOS, you've run Claude Code once, but forgot the commands. Copy-paste blocks are marked. Prompts to paste into Claude Code are in quote blocks.

---

## 0. What you're building

A local Python app, run from your machine, no web host. Daily scan of job boards → score → route base CV → draft CV/cover/screening → review in a local UI → log to CSV. Plus a single-JD paste pipeline.

Build order matches the prototype tabs. Build the engine first, UI last.

---

## 1. Prerequisites (one-time)

Open **Terminal** (Cmd+Space → type "Terminal").

Check Node (need v18+):

```bash
node --version
```

If missing or old, install Node:

```bash
brew install node
```

(No Homebrew? Install it first: https://brew.sh — one paste command on that page.)

Install Claude Code:

```bash
npm install -g @anthropic-ai/claude-code
```

Verify:

```bash
claude --version
```

---

## 2. Open the project in Claude Code

Your files already live here. Go to the folder and launch:

```bash
cd ~/Documents/Claude/Projects/"Job Applicator"
claude
```

That starts an interactive session **in this folder**. Claude Code can now read/write files here.

First run will ask you to log in (browser opens) or paste an API key. Follow the prompt once; it remembers.

### How you interact (the part you forgot)

- You **just type plain English** at the prompt and press Enter. No special syntax needed.
- Useful built-in commands (type with the slash):
  - `/help` — list commands
  - `/clear` — wipe the conversation context (use between unrelated tasks)
  - `/init` — have Claude write a `CLAUDE.md` describing the project (do this early; it gives Claude memory of the project's rules)
  - `/exit` — quit
- **Plan first, then build:** Press **Shift+Tab** to toggle *plan mode*. In plan mode Claude proposes a plan and waits — it won't edit files until you approve. Use it for each phase below.
- Claude will ask permission before running commands or editing files. Approve per-action, or pick "yes, and don't ask again for this" for trusted repeats.

---

## 3. Seed the project rules (do once)

Paste this into Claude Code:

> Read the file `job-applicator-ux-spec-v4.html` in this folder — it's the approved UX spec for the app we're building. Then run /init and write a CLAUDE.md that captures: this is a locally-run Python job-application agent; the architecture (board adapters by tier: api / browser-assisted / manual; scoring engine with editable weights; single base CV + cover letter for v1; drafting with click-to-review customisations; local CSV tracker; FastAPI + static UI matching the spec). Note the 3-base router and learning loop are later phases. Keep CLAUDE.md short.

Approve the file writes. From now on Claude Code reads `CLAUDE.md` automatically each session.

---

## 4. Build sequence

Build as a thin vertical slice, easy tier first. **Toggle plan mode (Shift+Tab) before each phase, review the plan, approve, then let it implement.** Test each phase before moving on. Order chosen so you see real scored jobs early and tackle the fragile browser automation last.

### Phase 0 — Ultra-MVP: paste a URL → review (no boards)

The smallest thing that's useful. No scanning, no board adapters. You paste a job URL, it scores and drafts, you review and download. Ship this first; everything else is an add-on.

> Plan and build a minimal version:
> - venv + deps: httpx, pydantic, fastapi, uvicorn, beautifulsoup4, pyyaml, python-dotenv.
> - `data/profile.yaml` — my scoring criteria + a `weights` block (full remote 30, skills & qualifications match 30, domain match 30, stage 10) summing to 100. Domain-match targets: PropTech, B2B SaaS, B2B2C, platforms, crypto/web3.
> - `cv/base-cv.md` and `cv/base-cl.md` — single placeholder CV and cover letter.
> - `engine/fetch.py` — given a job URL, fetch the page and extract title, company, location/mode, and description text.
> - `engine/score.py` — score the JD 0–100 using profile.yaml weights; return score + per-factor breakdown + one-line reason.
> - `engine/draft.py` — using the Anthropic API, draft a tailored CV over base-cv.md and a tailored cover letter over base-cl.md; record each change as base text + custom text + rationale.
> - `data/applications.csv` — append every pasted job: date, score, role, company, url, mode, status.
> - A minimal FastAPI UI from `job-applicator-ux-spec-v4.html`: the Jobs list (reads applications.csv) with "+ Paste JD" (URL → fetch → score → adds a row), and the Review page (CV/CL preview, click-to-compare, download as PDF). Skip Settings except the CV/CL upload and weights.

API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Run:

```bash
uvicorn ui.app:app --reload --port 8000
```

Paste a real job URL and confirm the full loop works end to end. Once this earns its keep, continue to Phase 1.

### Phase 1 — Skeleton + config (add boards, set criteria)

> Plan and scaffold the project. Create:
> - `pyproject.toml` (or requirements.txt) with: httpx, pydantic, fastapi, uvicorn, beautifulsoup4, python-dateutil, pyyaml.
> - folders: `adapters/`, `engine/`, `ui/`, `data/`, `cv/`.
> - `data/boards.yaml` — list of boards with: name, tier (api|browser|manual), endpoint_or_url, region, enabled. Make it easy to add a board by appending an entry.
> - `data/profile.yaml` — my criteria for scoring: geo gate (remote + UK hybrid), target domains (PropTech, B2B SaaS, B2B2C, platforms, crypto/web3), skills/qualifications, stage, plus a `weights` block (full remote 30, skills & qualifications match 30, domain match 30, stage 10) that sums to 100.
> - `cv/base-cv.md` and `cv/base-cl.md` — single placeholder CV and cover letter for v1.
> - `data/applications.csv` with headers for the tracker.
> Set up a Python venv and install deps. Show me how to activate it.

```bash
source .venv/bin/activate
```

### Phase 2 — Hoover jobs (API tier only)

> Build the `adapters/` layer, API tier only: Greenhouse, Lever, Ashby public job-board endpoints, plus Adzuna, RemoteOK, WeWorkRemotely (RSS). Each adapter returns a normalized Job: title, company, location, remote/hybrid/onsite, url, posted_date, description, source_board. Add a runner that loops enabled api-tier boards in boards.yaml and collects PM-matching jobs. Skip browser/manual tiers for now.

Test:

> Run the api-tier scan and show me the first 10 jobs found.

### Phase 3 — Score + downloadable CSV (checkpoint: look at output)

> Build `engine/filter.py` (PM-role include/exclude + geo gate from profile.yaml) and `engine/score.py`. Score 0–100 using the `weights` block from profile.yaml (not hardcoded). Output score + per-factor breakdown + one-line reason. Write the scored, ranked list to `data/applications.csv` with columns: date, score, role, company, board, mode, posted, url, status, plus the per-factor scores. Add a `scan` command that does fetch → filter → score → write CSV.

```bash
python -m engine.scan
```

**Stop and look.** Open `data/applications.csv` and sanity-check the scores before building anything else. Adjust weights in `profile.yaml` and re-run until the ranking feels right. Cheapest validation you'll get.

### Phase 4 — Single CV + CL, drafting customisation

> Build `engine/draft.py`. Using the Anthropic API, for a chosen job produce: a tailored CV as edits over `cv/base-cv.md` (each change recorded as base text + custom text + rationale), a tailored cover letter over `cv/base-cl.md`, and screening answers. Save drafts under `data/drafts/<company>-<role>/`. No 3-base router yet — single base.

API key in env (Claude will wire `python-dotenv`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### Phase 5 — UI: review, editable weights, CSV download

> Build the FastAPI app in `ui/` serving the screens from `job-applicator-ux-spec-v4.html`, wired to real data:
> - **Jobs** — flat ranked list from applications.csv; row click → Review; "+ Paste JD" → run_single; **Download CSV** button (exports current list + scores).
> - **Review** — full CV/CL/screening preview with changed spans highlighted; click a highlight → compare modal (custom / base / rationale / your version) → save; download CV and CL as PDF (server-side via the pdf/docx skills).
> - **Settings** — single CV + CL upload; filters; geo gate; **editable scoring weights** (one field per factor; on save, write back to profile.yaml and re-score); boards list with last-run status (OK/Failed/Stale).
> Run on localhost.

```bash
uvicorn ui.app:app --reload --port 8000
```

Open http://localhost:8000.

### Phase 6 — Browser-assisted tier (the hard part, last)

> Add the browser-assisted adapter using Playwright. For each browser-tier board, apply its native filters (PM role + remote/UK-hybrid) via a per-board recipe, read only the result list, and index rows passing headline+role+geo. Do NOT crawl full sites.

```bash
pip install playwright && playwright install chromium
```

Start with **one** board recipe (e.g. Otta or Himalayas), confirm it works, then add more. Expect per-board tuning.

### Later (only once the loop earns its keep)

- **3-base CV router** — `engine/router.py` picks crypto / CPO-VP / founding base by keyword priority. Replaces the single base.
- **Learning loop** — `data/overrides.jsonl` logs every "your version" edit; `data/style.md` (injected into drafting prompts) holds distilled phrasing rules; a `python -m engine.learn` command proposes new style.md lines and flags bullets to promote into the base. Overrides never auto-edit the base — only future drafts via style.md, which you control.
- **Scheduling** — see section 6.

---

## 5. Secrets & git hygiene

> Add a .gitignore that excludes .env, .venv, data/drafts, applications.csv, and any credentials. Initialize git. Confirm no secrets are tracked.

Store the API key in `.env`, never in code. If you later add board logins, use the macOS Keychain (`keyring` library) — not plaintext.

---

## 6. Run it daily (automatic)

Once the scan works manually, schedule it. Easiest reliable option on macOS is `launchd`. Ask:

> Create a launchd plist that runs `python -m engine.scan` every morning at 07:00, logging output to data/logs/. Give me the load command.

Load it (Claude will give the exact path):

```bash
launchctl load ~/Library/LaunchAgents/com.jobapplicator.daily.plist
```

Note: this only fires when your Mac is awake. For always-on, you'd need a host — out of scope for now.

---

## 7. Daily loop (how you'll actually use it)

1. Morning: scheduled batch run drafts overnight matches.
2. Open `http://localhost:8000` → Dashboard → see NEW roles.
3. Shortlist → click **Draft documents** for any not yet drafted.
4. Click a row → Review → check CV diff, cover letter, screening, adjust B2B/B2C lean → Approve.
5. Apply manually from the JD link (auto-submit stays off — ToS/ban risk).
6. Ad-hoc: Single JD tab → paste a JD you found → run.

---

## 8. Quick command reference

| Goal | Command |
|---|---|
| Open project in Claude Code | `cd ~/Documents/Claude/Projects/"Job Applicator" && claude` |
| Plan mode toggle | Shift+Tab (inside Claude Code) |
| Clear context | `/clear` |
| Project memory file | `/init` |
| Activate Python env | `source .venv/bin/activate` |
| Run the UI | `uvicorn ui.app:app --reload --port 8000` |
| Run daily scan now | `python -m engine.scan` (Claude will confirm exact path) |
| Update style from your edits (later phase) | `python -m engine.learn` |
| Quit Claude Code | `/exit` |

---

## Tooling decisions

- **Fetching stays in Python, not Zapier.** Hitting the API-tier feeds (Greenhouse/Lever/Ashby/Adzuna/RSS) is ~20 lines of `httpx`. Zapier doesn't make that easier and can't touch your local CV files, CSV, scoring, drafting, or review UI — the actual hard parts. Mixing a cloud trigger with a local engine adds a moving part and a subscription for the cheapest piece. Keep `fetch.py` local.
- **Zapier later, optional, only for notifications.** If you ever want "new score ≥ 85 → email/Slack me," Zapier (or a small local script) can do it. It is never the fetch layer.
- **LLM calls** (scoring nuance, drafting) use the Anthropic API directly from Python — full control over prompts, base CV, and `style.md`.

## Notes / assumptions

- *Based on assumption:* you'll keep human-in-the-loop approval; no auto-submit in v1.
- *Based on assumption:* target companies for the clean ATS APIs (Greenhouse/Lever/Ashby) come from a list you maintain; the aggregator boards are API-or-browser-assisted as tiered above.
- **Phase 0 ships first** — paste-URL → score → draft → review, no boards. It's usable on its own; everything after it is an add-on.
