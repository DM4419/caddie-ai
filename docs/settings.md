# Settings reference

Everything below is editable in the UI's **Settings** tab (and persisted to `data/profile.yaml`).

| Setting | What it controls | Notes |
|---|---|---|
| **Base documents** | Your CV + cover-letter backbone (`.md`/`.txt`) | Used to score *and* draft. The CL backbone uses `[BRACKETED]` slots. **Required** for meaningful output. |
| **AI fit rubric** (`fit_rubric`) | The **primary** scorer — plain-English rules the model follows to rank 0–100 (tiers, domains, deal-breakers) | Saving re-scores everything. Where most of your judgement lives. |
| **Candidate summary** (`candidate_summary`) | A LinkedIn-style paragraph the scorer reasons against alongside your CV | Edit in `profile.yaml`. |
| **Weighted factors** (`weights`) | The secondary, transparent score and **no-AI fallback**: per-factor weights (skills / domain / stage) + the keyword lists (`domains`, `skills`, `stage_signals`, `roles`) each factor matches | Hand-tunable; never hardcoded. **Location is a hard gate, not a scored factor.** |
| **Board search queries** (`role_queries`) | The job titles query-based boards search for | A shared default plus optional per-board overrides. |
| **Filters** | `geo_gate` (which work modes/regions to keep), spoken `languages` (+ boost/block), `recency_days` (the search horizon) | Rows outside the geo gate are dropped before storage. |
| **Boards** (`boards.yaml`) | The source registry: API / listing / browser / ATS entries, each enable-able | Add company ATS boards by slug; no key needed for Greenhouse/Lever/Ashby/etc. |
| **Skips / Strengths** | The learning anchors (see [learning-loop.md](how-it-works/learning-loop.md)) | Captured as you use the app; editable as plain markdown. |

## Optional keys in `.env`

```bash
ANTHROPIC_API_KEY=sk-ant-...           # required for AI scoring & drafting
# ANTHROPIC_MODEL=claude-sonnet-4-6    # drafting model (default)
# FIT_MODEL=claude-haiku-4-5           # fast/cheap fit scorer (default)
# ADZUNA_APP_ID=...  ADZUNA_APP_KEY=...   # free: https://developer.adzuna.com
# WEB3_CAREER_TOKEN=...                # https://web3.career/web3-jobs-api
```

See **[SETUP.md](../SETUP.md)** for install + troubleshooting.
