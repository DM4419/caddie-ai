# How drafting works

For a role worth pursuing, **Build Application Pack** runs an ordered sequence rather than a
single prompt — JD fit → screening questions → research → CV / cover letter / answers.

- **Research-first, shared framing.** Before any document is written, the app researches the
  role and company and produces one **framing** — angle to lead with, why-excited, the honest
  gap, cultural fit, emphasis. That same framing conditions the CV, the cover letter *and* the
  screening answers, so the whole pack tells one coherent story. Editable in a dedicated
  **Research tab**; rebuild any time.
- **Questions pulled in early.** Real screening questions are fetched up front (Greenhouse /
  Lever / Ashby / …) so the draft answers what the employer actually asks.
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
