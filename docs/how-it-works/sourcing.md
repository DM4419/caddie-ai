# Sourcing jobs

The interesting part isn't any one fetch — it's the **spread**. caddie-ai reaches the
market several complementary ways, each behind one normalised `Job` shape so scoring and
drafting don't care where a role came from.

- **Free remote feeds — via API.** Public/official feeds (RemoteOK, Working Nomads, Remotive,
  Himalayas, Arbeitnow, Web3 Career) queried straight through their APIs. Broad, cheap, robust.
- **Aggregators — via API.** Adzuna, **TheirStack** (347k+ sources, server-side dedup), and
  **Google Jobs** (via SerpAPI). The TheirStack adapter is **credit-aware**: it funnels by title +
  domain + region and fetches **incrementally** (a watermark + an id ledger) so it only pays for new roles.
- **VC portfolio talent networks.** Getro-powered boards (Index, Atomico, Seedcamp, Point Nine,
  Cherry, Speedinvest, …) — one adapter parses the board's `__NEXT_DATA__`, so a single source
  covers a whole portfolio of vetted startups.
- **Targeted niche portals — via allowed, filtered web indexing.** For boards without an API,
  a rate-limited Playwright tier renders the board's *own* filtered search and indexes **only
  the matching rows**. It never crawls a full site — it rides the board's filters.
- **Direct-employer companies — via their ATS.** Career pages on Greenhouse / Lever / Ashby /
  Workable / Recruitee / SmartRecruiters / Personio / **Teamtailor** are read through the ATS's
  no-key endpoints, so you track specific companies, not just aggregators.

(Plus the simplest path: paste a single job URL and it normalises that one role.)

**Reaching the real apply page.** An aggregator link often hides the real posting. When you build a
pack, caddie-ai follows the link to the underlying ATS (a browser pass gets past bot walls), updates
the role's URL, and fetches the live screening questions from the actual board.

## Depth of search = a recency horizon, not a fixed page count

caddie-ai doesn't fetch "the last N pages" and call it done. Each source is bounded by a
**time horizon** (`recency_days`, default **7**), and it's **incremental per source**:

- **First scan of a source** → auto-pulls the full backlog inside the horizon.
- **Every refresh after that** → only what's *new since that source's own last scan*, tracked
  per board, so a busy board and a quiet one advance independently.

Comprehensive first-time coverage without re-ingesting the same roles on every run; the
window is a single config value (`recency_days`) you can widen or narrow.

Code: `adapters/` (api · listing · browser tiers), `engine/fetch.py`, `engine/pipeline.py`.
