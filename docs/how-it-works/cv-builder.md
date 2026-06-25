# CV Builder (chat → structured CV → PDF)

A conversational CV builder at **`/cv-builder`**, for people with little CV experience
(school leavers, early-career) as well as professionals. It's a self-contained module
(`engine/cvbuilder/`) so it can later lift into a standalone product.

## The journey

1. **Import (optional)** — upload a PDF/Word/text CV or paste it; or start from scratch.
2. **Assess** — an imported CV gets a full-width scorecard: a one-sentence assessment, a
   one-sentence recommendation, and four 0–100 scores — **ATS · Domain · Results · Bio**.
3. **Capture the goal** (free text) — the goal drives sector, role type, and emphasis.
4. **Interview** — an adaptive chat fills the gaps, always probing for *quantifiable* results,
   and (for early-career) draws achievements out of school, part-time jobs, clubs, volunteering
   — never assuming a career. It does **not** ask for names/dates/contact (you type those
   straight onto the CV).
5. **Skills** — tap goal-/background-aware skill labels; a nudge keeps you toward ~7 core skills.
6. **Build** — a clean one-page CV with inline-editable **yellow placeholders** for names,
   dates and contact; export to PDF.

## Design rule

The LLM produces **data and chat turns — never the final layout**. Two LLM roles: an
**interviewer** (chat) and an **extractor/composer** (transcript + imported CV → `CVData`
JSON). A deterministic template (`render.py`) does the layout, matching the application CV's
visual style. Section order: **Profile › Skills › Work Experience › Projects › Education ›
Interests**.

Code: `engine/cvbuilder/` (`models.py` · `engine.py` · `render.py` · `store.py`),
UI at `ui/static/cvbuilder.html`, API under `/api/cvbuilder/*`.
