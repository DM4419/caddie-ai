<p align="center">
  <img src="assets/logo.svg" alt="Caddie AI" width="380">
</p>

<p align="center"><em>An open-source co-pilot for your job search — it scores roles against your CV, drafts tailored applications, helps you build a CV from scratch, and learns from your edits. Runs entirely on your machine.</em></p>

---

# caddie-ai

Applying to a lot of roles gets repetitive: read the posting, decide if it's worth it, then
retailor your CV and cover letter. caddie-ai takes the first pass — it scans job boards, scores
how well each role fits your CV, and drafts a tailored CV, cover letter, and screening answers.
You review, edit, and send everything yourself; **it never submits anything on its own.**

It runs entirely on your machine, and it **learns from the edits you make**, so over time its
drafts land closer to your voice.

> The name: like a golf caddie, it reads the course and hands you the right club. You still take
> every swing.

## What it does

A **closed loop**, not a one-shot generator — each stage feeds the next, and your own judgement
is captured and fed back in:

```
   ┌─────────── you skip / accept / edit / apply ───────────┐
   │                                                        │
   ▼                                                        │
┌────────┐  ┌────────┐  ┌──────────────────────────────┐  ┌─┴──────────────┐
│ SOURCE │─►│ SCORE  │─►│   BUILD APPLICATION PACK      │─►│ REVIEW & SUBMIT │
│ tiered │  │ fit vs │  │ JD fit → screening questions  │  │ (you, manually) │
│ boards │  │ your CV│  │ → research → CV / CL / answers │  └───────┬────────┘
└────────┘  └────────┘  └──────────────────────────────┘          │
        ▲                                                          │
        └──────── LEARNING LAYER (style · skips · strengths) ◄──────┘
```

Plus a **chat-based CV Builder** (`/cv-builder`) that interviews you — or assesses a CV you
upload — and produces a clean, ATS-friendly one-page CV.

![caddie-ai — the scored job board](assets/board.png)

![caddie-ai — a pre-generated application pack with the Screening tab open](assets/review-screening.png)

> Screenshots use **sample data** — a demo profile ("Alex Rivera") and example roles.

## How it works

Each part has a deliberate design choice behind it — the details live in their own docs:

- **[Sourcing jobs](docs/how-it-works/sourcing.md)** — three fetch tiers (API · filtered browser
  · direct ATS) behind one `Job` shape; coverage bounded by a recency horizon, not a page count.
- **[Fit scoring](docs/how-it-works/scoring.md)** — semantic 0–100 fit vs your CV, a transparent
  weighted cross-check, and a verbatim requirement-by-requirement check. Location is a gate, not
  a score.
- **[Drafting the pack](docs/how-it-works/drafting.md)** — research-first shared framing →
  per-gig base-CV routing → CV / cover letter / answers, with change provenance and no-invention
  guardrails.
- **[Board / ATS optimisation](docs/how-it-works/board-optimization.md)** — tunes the pack to how
  Greenhouse / Lever / Ashby actually screen.
- **[The learning loop](docs/how-it-works/learning-loop.md)** — your edits + reasons → distilled
  rules → the next draft. The part that compounds.
- **[CV Builder](docs/how-it-works/cv-builder.md)** — conversational, import-and-assess, →
  structured data → deterministic template → PDF.
- **[Architecture](docs/how-it-works/architecture.md)** · **[Settings reference](docs/settings.md)**

## Quick start

```bash
git clone https://github.com/DM4419/caddie-ai && cd caddie-ai
cp .env.example .env          # then add your Anthropic API key
./run.sh                      # sets up venv + deps + browser, then opens the app
```

Opens **http://127.0.0.1:8000**. The repo ships a **sample** CV and profile so it runs out of
the box — replace them with your own (Settings tab) to make it meaningful. Full install,
manual setup, and troubleshooting: **[SETUP.md](SETUP.md)**.

## Safety & privacy

- **No auto-submit, ever** — the agent drafts and fills; you submit.
- **Local-first** — runs on `localhost`; no web host, no telemetry. Your CV, history, and API
  key never leave your machine.
- **Secrets stay in `.env`** (gitignored); the repo ships only `.env.example`.
- **The browser tier never crawls full sites** — it only rides a board's own filters.
- **Your learning files are yours** — the tool appends and distils, but never silently
  overwrites your curated rules or edits your base CV.

## Tech stack

Python 3.11+ · FastAPI · pydantic · httpx · BeautifulSoup · Playwright · the Anthropic API
(Sonnet for drafting, Haiku for fast scoring, with prompt caching).

Shared as a portfolio piece — this public copy contains **sample data only**, no real
application history or credentials.

## License

[MIT](LICENSE)
