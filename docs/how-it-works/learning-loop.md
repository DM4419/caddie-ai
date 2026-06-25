# The learning loop (it improves every time you edit)

The heart of caddie-ai. First drafts are decent; the tenth are *yours*, because every
correction is captured as a reusable preference and fed back into drafting and scoring.
**Nothing here ever auto-edits your base CV** — the loop only influences *future drafts*
through files you control.

## 1 · Capture — human-initiated feedback channels

| Channel | Trigger | Stored in | Becomes |
|---|---|---|---|
| **Accepted edits** | Edit a drafted bullet/paragraph in the compare modal | `data/style.md` (append-only *AI-suggested → changed-to → reason*) | Voice & wording preferences |
| **Bulk CL revision** | Paste a fully rewritten cover letter | paragraph-diffed; the model infers a reason per change to confirm, then appends to `style.md` | Same, in bulk |
| **Skips** | Pass on a role with a reason | `data/skips.md` | **Negative anchors** — down-rank similar roles |
| **Strengths** | List what you're strong at | `data/strengths.md` | **Positive anchors** — always treated as *met* |

## 2 · Distil — raw log → compact, reusable knowledge

`engine/learndistill.py` turns the raw `style.md` log into two distilled layers *without
modifying the raw log*:
- **`style-rules.md`** — a tight, de-duplicated do/don't rule set in your voice (VOICE & TONE /
  HARD DON'TS / CV / COVER LETTER).
- **`style-examples.md`** — accepted edits grouped *per application*, so drafting samples a
  **balanced** set rather than over-fitting to one company.

Re-distillation runs **right before each generation, but only when something changed** (a cheap
timestamp check) — never on a schedule that could draft from an obsolete rule set.

## 3 · Reinforce — fold into the next generation

On every draft, `draft._learning_block` prepends to the system prompt: distilled **rules**
(followed strictly) + a **balanced** set of accepted-edit examples (≤2 per company) + the
**freshest** raw edits + your **strengths**. Skips feed the scorer as negative anchors;
strengths feed both scorer and drafter as positive anchors.

> **Design note:** the learning files are *yours*. The tool appends to the raw log and
> regenerates the distilled layers, but treats your curated rules as authoritative — it never
> silently overwrites what you've hand-edited.

On submit, a **learning recap** shows exactly what that application taught and whether it
changed your global rules — nothing is a black box.
