# Caddie AI — Setup & Customisation guide

A step-by-step walkthrough to get Caddie AI running and tuned to **you**: install,
add your credentials, upload your documents in the right order, then customise the
scoring and sources. Runs locally; nothing is ever auto-submitted.

- [1. Install](#1-install) · [2. Credentials (.env)](#2-credentials-env)
- [3. First-run setup, in order](#3-first-run-setup-in-order) — the important part
- [4. Daily use](#4-daily-use) · [5. Reviewing learnings & guardrails](#5-reviewing-learnings--guardrails)
- [6. Optional board keys](#6-optional-board-api-keys) · [7. Reset a handed-over copy](#7-reset-a-handed-over-copy) · [8. Troubleshooting](#8-troubleshooting)

---

## 1. Install

**Prerequisites:** Python 3.9+ (3.11+ recommended), an
[Anthropic API key](https://console.anthropic.com), macOS/Linux, ~300 MB free
(Playwright downloads a headless browser for the browser-tier boards).

```bash
git clone https://github.com/DM4419/caddie-ai && cd caddie-ai
cp .env.example .env            # you'll fill this in next
./run.sh
```

`run.sh` creates the virtualenv, installs dependencies + the Playwright browser on the
first run, then starts the app and opens **http://127.0.0.1:8000**. Later runs just start it.

<details><summary>Manual install instead of <code>run.sh</code></summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
uvicorn ui.app:app --port 8000
```
</details>

---

## 2. Credentials (.env)

All secrets live in `.env` (gitignored — never commit or share it; each person needs
their own key). Edit it with any editor, or from the CLI:

```bash
# REQUIRED — AI scoring & drafting (https://console.anthropic.com)
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env

# OPTIONAL — only for these specific boards:
echo 'ADZUNA_APP_ID=...'      >> .env   # free: https://developer.adzuna.com
echo 'ADZUNA_APP_KEY=...'     >> .env
echo 'WEB3_CAREER_TOKEN=...'  >> .env   # https://web3.career/web3-jobs-api

# OPTIONAL — model overrides (sensible defaults already set):
echo 'ANTHROPIC_MODEL=claude-sonnet-4-6' >> .env   # drafting model
echo 'FIT_MODEL=claude-haiku-4-5'        >> .env   # fast/cheap fit scorer
```

Direct-employer boards on Greenhouse / Lever / Ashby / Workable / Recruitee /
SmartRecruiters / Personio need **no key**. Restart the app after editing `.env`.

The app runs without an API key, but you'll only get the weighted (keyword) score and
untailored base documents until you add one.

---

## 3. First-run setup, in order

Open **http://127.0.0.1:8000** and go to the **Settings** tab (left nav). Do these in
sequence — later steps assume the documents from the earlier ones exist.

### Step A — Matching CV (required, do this first)
**Settings → CV library & documents → Matching CV.** Upload a `.pdf`, `.docx`, `.md`, or
`.txt` (text is extracted), or click **paste text**. This is the CV every job is **scored**
against — it is *not* sent in applications, so keep it complete and factual.

> Until this is uploaded, the Jobs tab shows a "⚠️ Upload your base CV" banner and scores
> stay low.

### Step B — Base cover letter backbone
**Settings → CV library & documents → Base cover letter.** Upload/paste your cover-letter
*backbone*: settled prose in your voice with `[BRACKETED]` slots (e.g. `[ROLE]`,
`[COMPANY]`, `[WHY EXCITED]`). The drafter keeps this prose and only fills the slots — it
never rewrites your voice, and never invents a fact to fill a slot.

### Step C — Application CV variants (recommended)
**Settings → CV library & documents → Application CVs → + Add a variant.**
These are the flavoured CVs the drafter actually tailors and sends (the Matching CV from
Step A is only for scoring). Add one per direction you target, and tick the **flags** that
should auto-select it, e.g.:

| Variant name | Tick these flags |
|---|---|
| Crypto / Web3 | Web3 / Blockchain / crypto |
| EIR / Founder | EIR / Founder-in-Residence · Ex-founders welcome |
| ex-Founder · Senior IC | 0→1 build · Ex-founders welcome |

When you draft, Caddie AI auto-picks the variant whose flags best match the role (you can
override per draft from the "tailored from" dropdown). *(Optional:* add a **Reference PDF**
— one human-formatted master kept for layout/export reference.)*

### Step D — Candidate summary
Edit `data/profile.yaml` → `candidate_summary` (a LinkedIn-style paragraph about you; no UI
field yet). The scorer reasons against this **plus** your Matching CV.

### Step E — AI fit rubric (your priorities)
**Settings → AI fit scoring → Rubric.** This plain-English rubric is the **primary** scorer
— it decides the 0–100 fit score. Edit it to encode your tiers, target domains, and
deal-breakers, then **Save rubric & re-score** (re-scores every stored job). Most of your
judgement lives here.

### Step F — Weighted factors (secondary score / no-AI fallback)
**Settings → Weighted factors.** Per-factor weights (**must sum to 100**: remote / skills /
domain / stage) plus the keyword lists each factor matches. This powers the "⚖ Wt" score and
the "skills considered" chips, and is the fallback when no API key is set. **Save & re-score.**

### Step G — Filters (gates applied while scanning)
**Settings → Filters.** Set the **geo gate** (which work modes/regions to keep — US options
off by default; roles outside the gate are dropped before storage), spoken **languages**, and
the **recency** window in days (the search horizon). **Save filters.**

> Note: location/work-mode is handled here and in the remote score — it is *not* listed as a
> qualification "gap".

### Step H — Board search queries
**Settings → Board search queries.** One job-title query per line; the default applies to all
query-based boards (Adzuna, recruiters). Add per-board overrides if a board needs different terms.

### Step I — Add your sources
**Boards tab → Add a source.** Paste a job board, company careers page, or recruiter URL — the
ATS is auto-detected (Greenhouse/Lever/Ashby/Workable/Recruitee/SmartRecruiters/Personio) and
scanned, no key needed. Build up the companies you actually want to track.

---

## 4. Daily use

On the **Jobs** tab:

- **+ Paste a role** — paste one job URL or description to fetch + score it immediately.
- **↻ Refresh all sources** — scans every enabled board (incremental: first scan pulls the
  recency window, later scans only what's new per board). Use a group's **↻ Scan group** for a
  targeted scan.
- **Tabs & filters** — Active · ⚑ Founder-fit · 🎙 Voice AI · ★ Bookmarked, plus a **Status…**
  dropdown (Applied / Skipped / Archived). Sort by AI score, Weighted score, seniority, date, or salary.
- **Open a role** to review: the fit breakdown, the JD's requirements classified
  **match / stretch / gap** against your CV, and the **Unmet** qualification gaps.
  - *Aggregator link?* (e.g. Adzuna) use **🔎 Find the real application & fetch its questions** to
    follow it to the real listing.
  - **About this application** — fill the cover-letter slots, or click **✨ Research & pre-fill**
    to draft them from the JD/company (review before using).
  - **Tabs (CV / Cover letter / Screening)** each have their own actions; the Screening tab has a
    **copy icon** per answer for pasting into the form.
  - **↻ Regenerate** to draft (auto-picks the best CV variant), **✎ Edit** / **✓ Accept all edits**,
    then **⬇ Download** (Docx for ATS uploads, PDF, or MD).
  - **🔗 People** — shortlist who to contact at the company: target personas (hiring lead,
    recruiter, founder, peer) with ready-made Google/LinkedIn searches you open in your browser,
    plus a connection-note draft. (Set `BRAVE_API_KEY` in `.env` to list names/roles inline.)
- **Skip** (plain — no ranking impact) or **Skip ▾ → Skip + Train…**, which teaches the scorer:
  *fewer like this* (down-rank similar) or *more like this* (promote similar). **Approve & mark
  applied** when you've sent it.

Every edit you accept teaches Caddie AI your style; the next drafts need fewer corrections.

---

## 5. Reviewing learnings & guardrails

Caddie AI both **learns from your edits** and applies **fixed guardrails** — both are inspectable.

### What it has learned (and how to prune it)
Everything it learns lives in plain files under `data/` — yours to read, edit, or delete:

| File | What it holds | How it's used |
|---|---|---|
| `style.md` | raw, append-only log of every edit you accepted (*AI suggested → changed to → your reason*) | the source of truth |
| `style-rules.md` | a compact, de-duplicated do/don't rule set distilled from `style.md` | followed strictly on every draft |
| `style-examples.md` | accepted edits grouped per application (balanced sampling) | voice examples for drafting |
| `skips.md` | why you passed on roles, *fewer like this* (negative anchors) | down-ranks similar roles when scoring |
| `likes.md` | roles you skipped but want *more like this* (positive anchors) | up-ranks similar roles when scoring |
| `strengths.md` | things you're strong at (positive anchors) | treated as **met**; lifts the score |

- **Review & prune in the UI:** **Settings → Learned preferences** lists every captured edit as a
  card — **✕** forgets one, **Clear all** wipes them. Pruning rewrites `style.md`.
- **Export for an offline read:** in a role's review pane, **⬇ Download → Learnings summary**
  (Docx / PDF / MD) gives you the full set.
- **Re-distil:** the rule set is rebuilt from `style.md` **automatically right before each draft,
  but only when you've changed something** (no stale schedule) — so drafts always reflect your
  latest edits. You can also trigger a rebuild manually.
- `skips.md`, `likes.md` and `strengths.md` are captured as you use the app (**Skip ▾ → Skip + Train**
  writes a skip or a like depending on the direction; strengths you maintain) and are all
  editable directly as markdown.

### The guardrails (fixed, in code)
These don't change with learning — they're enforced in `engine/draft.py` (the drafting system
prompt + post-processing) and the project's hard rules:

- **No auto-submit, ever** — Caddie AI drafts and fills; *you* send every application.
- **No invention** — never fabricates an employer, date, metric, or "why-excited" fact; an
  un-fillable cover-letter slot becomes a visible `[ tell me … ]` placeholder, not a made-up claim.
- **Your base CV is never auto-edited** — learnings only steer *future drafts*, never your source docs.
- **Cover-letter backbone is near-sacred** — your prose/voice is kept; only the `[SLOTS]` are filled.
- **House style enforced in code** — em-dashes stripped from prose; standard CV page breaks inserted.
- **Location isn't a "gap"** — geography is handled by the geo gate + remote score, never listed as unmet.
- **Local & private** — runs on `localhost`; your CV, history, and API key never leave your machine.

To change the *soft* rules, edit `style-rules.md` (or prune the edits that produced them). The
*hard* rules above live in code by design.

---

## 6. Optional board API keys

See [Step 2](#2-credentials-env) — Adzuna (free) and Web3 Career need keys in `.env`; the ATS
boards do not. Restart the app after adding keys.

---

## 7. Reset a handed-over copy

If you were given a folder containing someone else's data, clear it before using your own:

```bash
rm -f data/jobs/*.json data/applications.csv data/state.json   # saved jobs + tracker + upload state
rm -f cv/base-cv.md cv/base-cl.md cv/apps/*.md                 # their CVs + variants
```

Then redo [Step 3](#3-first-run-setup-in-order). (The curated board list in `data/boards.yaml`
is kept — edit it or the Boards tab as you like.)

---

## 8. Troubleshooting

- **`ERR_CONNECTION_REFUSED`** — the server isn't running; `./run.sh`.
- **Scores all low / "no AI key"** — check `ANTHROPIC_API_KEY` in `.env`, then restart.
- **"tailored from Matching CV"** — you haven't added Application CV variants yet (Step C); the
  drafter falls back to the Matching CV until you do.
- **A board won't scan** — some sites are bot-protected (Glassdoor/LinkedIn) and can't be scraped;
  add the company's own careers/ATS page instead.
- **Screening questions can't be fetched** — use **🔎 Find the real application** on the role, or
  paste them via the Screening tab's **↑ Screening Qs** (works for any ATS).
- **Playwright errors** — re-run `python -m playwright install chromium`.
