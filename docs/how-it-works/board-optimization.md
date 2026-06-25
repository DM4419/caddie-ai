# Board / ATS optimisation

The application pack is tuned to **how the destination board screens**. caddie-ai detects the
ATS from the job URL and feeds board-specific guidance into the draft prompt, plus a "what to
focus on" panel in the review UI. It never fabricates to fit — the directives say so explicitly.

## Mechanics it optimises for

- **Greenhouse** — no algorithmic ranking; a human rates you against a scorecard
  (Definitely Not → Strong Yes) built from the requirements, after a ~30–45s scan; knockout
  questions can auto-filter. → *One quantified evidence bullet per requirement; top-load metrics;
  answer knockouts truthfully.*
- **Lever** — keyword search stems words but does **not** expand acronyms; scores visible to
  recruiters; newer AI (Talent Fit / VONQ) screens at application. → *Write every key term both
  ways — "Search Engine Optimization (SEO)"; dense, exact keyword coverage.*
- **Ashby** — AI checks the resume against recruiter-defined criteria and returns a fit level
  **with citations** (deliberately no numeric rank); humans score 1–4 (3+ = pass); PII redacted
  before the AI sees it. → *Make each criterion citable — mirror its wording right beside concrete
  evidence; one unambiguous bullet per criterion.*

## Universal rules (every application)

- Pass knockout/screening questions truthfully (years, work auth, timezone, comp) — they
  override resume quality.
- One evidence bullet per stated requirement (aim 80%+ coverage of the must-haves).
- Mirror the posting's exact nouns/verbs; spell out **and** abbreviate key terms.
- Quantify and top-load: 5+ metrics, strongest in the top half.
- Export as PDF and check the parse preview.

Unrecognised boards fall back to the universal rules. Code: `engine/boards_optimize.py`
(reuses `adapters/ats.py` for detection); injected into `engine/draft.py`, surfaced in the
review UI.
