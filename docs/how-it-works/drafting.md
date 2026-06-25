# How drafting works

For a role worth pursuing, **Build Application Pack** runs an ordered sequence rather than a
single prompt — JD fit → screening questions → research → CV / cover letter / answers.

- **Research-first, shared framing.** Before any document is written, the app researches the
  role and company and produces one **framing** — angle to lead with, why-excited, the honest
  gap, cultural fit, emphasis. That same framing conditions the CV, the cover letter *and* the
  screening answers, so the whole pack tells one coherent story. Editable in a dedicated
  **Research tab**; rebuild any time.
- **Questions pulled in early.** Real screening questions are fetched up front from the *real*
  apply page (it follows an aggregator link to the underlying Greenhouse / Lever / Ashby board),
  so the draft answers what the employer actually asks.
- **Editable drafting doctrines.** The voice and structure rules for the **cover letter**, **CV
  summary**, and **screening answers** live in plain spec files, loaded into the prompt
  (**prompt-cached**) before your learned preferences (which win on conflicts). Each is **scoped to
  its own document** so the letter's structure can't leak into the answers, and all three are
  editable in **Settings → Drafting doctrine**.
- **Strengthen your match.** In the JD-fit tab, each requirement is mapped to a **CV-grounded
  bullet** you accept or rewrite — tagged with the CV experience it belongs under and a
  *include-in-cover-letter* toggle. On rebuild, your bullets are woven into the right CV role and,
  if ticked, the letter. Never fabricated beyond what you supply.
- **Per-gig base-CV routing.** Keep several base CVs, each flagged for a kind of role (Founder,
  EIR / founder-welcome, Web3, Voice AI, 0→1). The drafter auto-picks the closest-fit base from
  the job's detected flags (`_suggest_app_cv`); you can override per draft.
- **ATS-aware optimisation.** The pack is tuned to how the destination board screens
  (Greenhouse scorecard, Lever keyword search, Ashby citations). See
  [board-optimization.md](board-optimization.md).
- **Provenance on every change.** Each edited span is emitted as
  `<mark class="chg" data-base="ORIGINAL" data-rat="why">new text</mark>`, so the review UI
  shows an inline diff and a compare modal — exactly what changed and why.
- **No-invention guardrails.** Never fabricates employers, dates, metrics, or a why-excited
  detail; unknown facts become a visible `[ tell me … ]` placeholder.
- **Graceful degradation.** No API key (or a failed call) falls back to rendering your base
  documents untailored, so the UI always works.
- **House style in code**, not just prompt: em-dashes stripped from prose, standard CV page
  breaks inserted automatically.

Code: `engine/draft.py`, `engine/questions.py` (screening), `engine/clchanges.py` (bulk CL diff).
