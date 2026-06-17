# caddie-ai — Setup & Customization

A locally-run agent that scans job boards, scores roles against **your** CV with AI,
and drafts tailored applications for you to review. Runs on your Mac; nothing is
auto-submitted.

---

## 1. Prerequisites
- **Python 3.9+** (3.11+ recommended) and `pip`
- An **Anthropic API key** → https://console.anthropic.com  (needed for AI scoring & drafting)
- macOS/Linux. ~300 MB free (Playwright downloads a headless browser)

---

## 2. Quick start (easiest)

```bash
cd caddie-ai
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env   # your key: https://console.anthropic.com
./run.sh
```

`run.sh` installs everything on the first run (virtualenv, dependencies, browser),
then starts the app and opens http://127.0.0.1:8000. Later runs just start it.

<details><summary>…or set it up manually</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium     # for the browser-tier boards
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
uvicorn ui.app:app --port 8000
```
</details>

> **Security:** never commit or share your `.env`. Each person needs their own key.
> Board logins are never stored in plaintext; the app never submits applications for you.

---

## 3. Make it *yours* (reset the shared data)

The folder you received contains the previous owner's CV, profile and saved jobs.
Reset them:

```bash
# clear saved jobs + tracker + upload state (keeps the curated board list)
rm -f data/jobs/*.json data/applications.csv data/state.json
rm -f cv/base-cv.md cv/base-cl.md
```

Then either edit **`data/profile.yaml`** directly, or set everything in the **Settings**
tab of the UI (next section). The two things that matter most:
- `candidate_summary` — a paragraph about you (the AI scores against this + your CV).
- `fit_rubric` — how the AI should rank roles (your priorities/tiers).

---

## 4. Run it (every time)

```bash
./run.sh
```
Opens **http://127.0.0.1:8000** automatically. (Manual equivalent:
`source .venv/bin/activate && uvicorn ui.app:app --port 8000`.)

---

## 5. Customize (all in the UI → **Settings**)

| Setting | What it does |
|---|---|
| **Base documents** | Upload your CV + cover letter (.md/.txt). Used to score & draft. **Required.** |
| **AI fit scoring → Rubric** | The primary scorer. Plain-English rules the AI follows to rank 0–100. Edit to change your priorities (tiers, domains, deal-breakers). Save re-scores everything. |
| **Weighted factors** | The secondary "⚖ Wt" score (and the no-AI fallback): per-factor weights (sum to 100) + the keyword lists each matches. |
| **Board search queries** | The job titles query-based boards (Adzuna, recruiters) search for. |
| **Filters** | Geo gate (which work modes/regions to keep — US options are off by default), spoken languages, recency window (days). |

Also edit `candidate_summary` in `data/profile.yaml` (no UI field yet).

---

## 6. Daily use (Jobs tab)
- **+ Paste a role** — paste a single job URL/description to score it.
- **Add a source** (Boards tab) — paste a job board, company careers page, or recruiter URL; we detect the ATS (Greenhouse/Lever/Ashby/Workable/Recruitee/SmartRecruiters/Personio) and scan it.
- **↻ Refresh all sources** — scans every board (slow if you have many company/browser boards; one-line progress shows). Use a group's **↻ Scan group** for a targeted scan.
- **Tabs/filters** — Active · ⚑ Founder-fit · 🎙 Voice AI · Applied · Skipped · Archived. Sort by AI score, Weighted score, seniority, date, or salary.
- Click a row → review the AI fit breakdown, matched/unmet requirements, then **generate & download** a tailored CV + cover letter.

---

## 7. Optional board API keys (add to `.env` if you want these boards)
```bash
ADZUNA_APP_ID=...        # https://developer.adzuna.com (free)
ADZUNA_APP_KEY=...
WEB3_CAREER_TOKEN=...     # https://web3.career/web3-jobs-api
# model overrides (optional):
FIT_MODEL=claude-haiku-4-5          # scorer (fast/cheap); default already set
ANTHROPIC_MODEL=claude-sonnet-4-6   # drafting model
```
Greenhouse/Lever/Ashby/Workable/Recruitee/SmartRecruiters/Personio boards need **no key**.

---

## 8. Troubleshooting
- **`ERR_CONNECTION_REFUSED`** → the server isn't running; start it (step 4).
- **Scores all low / "no AI key"** → check `ANTHROPIC_API_KEY` in `.env`.
- **A board won't scan** → some sites are bot-protected (e.g. Glassdoor/LinkedIn) and can't be scraped; use the company's own careers/ATS page instead.
- **Playwright errors** → re-run `python -m playwright install chromium`.
