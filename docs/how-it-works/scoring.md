# How fit is evaluated (JD ↔ your CV)

Scoring reasons over your CV + summary **semantically** — not literal keyword overlap (a deep
0→1 AI builder scores high on "Qualifications" even if the JD never uses those words). It runs
at two depths plus an on-demand requirement check.

**1 · Ranking (every role).** A batched pass (default `claude-sonnet-4-6`, configurable via
`FIT_MODEL`; candidate profile **prompt-cached** so repeat runs are cheap) returns per role:
a **0–100 fit score**, a one-line reason, up to three **verbatim "drivers"** quoted from the
JD, an inferred location, and up to three **unmet** tags. A transparent **weighted score**
(skills / domain / stage — weights you control) runs alongside as a cross-check and as the
**no-API fallback**, so the app still ranks without a key.

**2 · Deep analysis (on demand, per role).** A *score rationale*, *best-fit* and *shortcomings*
paragraphs, the profile **skills it matched**, the **unmet** requirements (strictly
JD→candidate), and a four-dimension **breakdown** (Qualifications · Domain · Role & stage · …),
each scored 0–100 with a one-line note.

**3 · Requirement-by-requirement check.** `classify_requirements` extracts 5–15 **verbatim**
requirement phrases from the JD and tags each **match / stretch / mismatch** — quoted exactly
so the UI can highlight them in place. The quick "do I actually clear the bar?" read.

> **Location/timezone is a gate, not a score.** Geography is handled by a **timezone-band gate**
> (keep roles whose region falls in a collaboration window, e.g. UTC 0–5; multi-region remote roles
> survive if *any* listed region is in-band) plus the work-type filter — it never moves the fit
> score or appears as a "gap". A **language gate** works the same way: a fluency requirement in a
> language you don't speak caps the score and is flagged on its own axis, never folded into fit.

> **Full-JD enrichment.** Aggregator snippets are thin, so after a scan the app auto-fetches the
> **full JD** for high scorers (above a settable threshold on either scale) and re-applies the geo
> and language gates upfront — catching a US-only or "needs fluent German" role before you open it.

**The link to drafting:** matched strengths and honest gaps pass straight into the draft
prompt (`role_fit_block`) — strengths surface in the CV and letter; the most relevant gap is
named candidly and bridged with your closest true experience. Never hidden, never faked.

Guards: the scorer is told to **spread** scores (no clustering), quote the JD rather than
paraphrase, and **never invent** a requirement the JD doesn't state.

Code: `engine/score.py` (weighted), `engine/fitscore.py` (AI fit + requirements).
