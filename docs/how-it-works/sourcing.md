# Sourcing jobs

The interesting part isn't any one fetch — it's the **spread**. caddie-ai reaches the
market three complementary ways, each behind one normalised `Job` shape so scoring and
drafting don't care where a role came from.

- **Global aggregator boards — via API.** Public/official feeds (e.g. RemoteOK, Working
  Nomads, Adzuna, Web3 Career) queried straight through their APIs. Broad reach, cheap, robust.
- **Targeted niche portals — via allowed, filtered web indexing.** For boards without an API,
  a rate-limited Playwright tier renders the board's *own* filtered search and indexes **only
  the matching rows**. It never crawls a full site — it rides the board's filters.
- **Direct-employer companies — via their ATS.** Career pages on Greenhouse / Lever / Ashby /
  Workable / Recruitee / SmartRecruiters / Personio are read through the ATS's no-key
  endpoints, so you track specific companies, not just aggregators.

(Plus the simplest path: paste a single job URL and it normalises that one role.)

## Depth of search = a recency horizon, not a fixed page count

caddie-ai doesn't fetch "the last N pages" and call it done. Each source is bounded by a
**time horizon** (`recency_days`, default **7**), and it's **incremental per source**:

- **First scan of a source** → auto-pulls the full backlog inside the horizon.
- **Every refresh after that** → only what's *new since that source's own last scan*, tracked
  per board, so a busy board and a quiet one advance independently.

Comprehensive first-time coverage without re-ingesting the same roles on every run; the
window is a single config value (`recency_days`) you can widen or narrow.

Code: `adapters/` (api · listing · browser tiers), `engine/fetch.py`, `engine/pipeline.py`.
