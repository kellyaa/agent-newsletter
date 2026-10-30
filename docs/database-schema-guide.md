# Database Schema and Querying Guide

The pipeline's state is persisted to a single SQLite file (`state.db`) on the `content` branch. This guide explains what the schema contains, how to query it, and how to interpret results for debugging and monitoring.

## Quick Start: Common Queries

```bash
# See recent run summaries
sqlite3 editions/agents/state.db \
  "SELECT date, items_featured, items_papers, items_news, items_blogs, \
          tokens_in, tokens_out FROM runs ORDER BY date DESC LIMIT 7;"

# Check if today's run completed successfully
sqlite3 editions/agents/state.db \
  "SELECT date, outcome, failed_stage, exit_code FROM run_attempts \
   WHERE date = date('now') ORDER BY start_time DESC LIMIT 1;"

# Find an item by title (partial match)
sqlite3 editions/agents/state.db \
  "SELECT id, title, source, status, score FROM items \
   WHERE title LIKE '%llm%agents%' LIMIT 5;"

# See featured items from yesterday
sqlite3 editions/agents/state.db \
  "SELECT title, source, score, section FROM items \
   WHERE status = 'featured' AND published_at >= date('now', '-1 day') \
   ORDER BY score DESC;"
```

## Schema Overview

### Main Tables

#### `items` — Every fetched item
The core table tracking articles, papers, releases, and discussions fetched from feeds.

| Column | Type | Meaning |
|--------|------|---------|
| `id` | TEXT PRIMARY KEY | SHA256 hash of canonical URL (dedup key) |
| `source` | TEXT | Feed source identifier (`arxiv:cs.ai`, `hn:agents`, `rss:latent_space`, etc.) |
| `url` | TEXT | Original URL (may not be canonical) |
| `canonical_url` | TEXT | Normalized URL (lowercase host, no utm params, no trailing slash) |
| `title` | TEXT | Item title/headline |
| `author` | TEXT | Author name(s) if available |
| `published_at` | TIMESTAMP | Date the source item was published |
| `fetched_at` | TIMESTAMP | When the pipeline fetched it |
| `raw_text` | TEXT | Abstract, summary, or first 500 chars of body text |
| `section` | TEXT | Assigned section: `papers`, `news`, or `blogs` |
| `section_override` | TEXT | Per-source section override (see `editions/<slug>/sources.yaml`) |
| `status` | TEXT | Current state (see lifecycle below) |
| `score` | REAL | Ranker score 0-10 (NULL if unscored) |
| `tags` | TEXT | JSON array of tags (`["frameworks", "evals", ...]`) |
| `one_line_why` | TEXT | Ranker's one-sentence justification |
| `appearances` | INTEGER | Times this item has re-entered the candidate pool (cross-day dedup) |

#### Item Lifecycle: `status` Values

An item's journey through the pipeline is tracked by its `status`:

- **`new`** — Just fetched; not yet processed by prefilter.
- **`candidate`** — Passed prefilter gates (recency, keywords, reputation); ready for the ranker to score.
- **`featured`** — Ranker scored ≥7 (papers) or ≥6 (news/blogs); included in published issue.
- **`appendix`** — Ranker scored in the appendix range (5-6 for papers, 4-5 for news/blogs); listed at end of issue but not summarized.
- **`dropped`** — Ranker scored below threshold; explicitly excluded.
- **`published`** — Featured or appendix item that made it into the published issue and was sealed (will not re-rank).
- **`expired_unjudged`** — Paper that aged out of the 7-day candidate pool before the ranker ever scored it (never-judged expiry; issue #289).

**Key insight:** `featured` and `published` are different. `featured` means "scored high and slated for the issue." `published` means "made it into the issue file that was pushed." An item could be `featured` but fail to make it into the issue file if there's a write error (rare but possible).

#### `runs` — Daily pipeline execution
One row per successful published issue, with aggregate metrics.

| Column | Type | Meaning |
|--------|------|---------|
| `date` | DATE | Issue publication date (YYYY-MM-DD) |
| `items_fetched` | INTEGER | Total items collected from all sources |
| `items_candidate` | INTEGER | Items that passed prefilter and entered ranking |
| `items_featured` | INTEGER | Total featured items across all sections |
| `items_papers` | INTEGER | Featured papers |
| `items_news` | INTEGER | Featured news items |
| `items_blogs` | INTEGER | Featured blog items |
| `items_appendix` | INTEGER | Appendix items (uncertain tier) |
| `items_dropped` | INTEGER | Items scored below threshold |
| `duration_seconds` | REAL | Wall-clock runtime |
| `tokens_in` | INTEGER | Total LLM input tokens (summed across ranker × 3 sections + writer) |
| `tokens_out` | INTEGER | Total LLM output tokens |
| `cost_usd` | REAL | Estimated cost (currently NULL; cost is priced downstream from tokens) |
| `papers_expired_unjudged` | INTEGER | Papers that aged out before the ranker scored them |
| `papers_prerank_cut` | INTEGER | Unscored papers removed by the `PAPER_PRERANK_CAP` (issue #289) |
| `notes` | TEXT | JSON object with per-model token breakdown: `{"ranker": {"model": "...", "tokens_in": N, "tokens_out": M}, ...}` |

#### `run_attempts` — Every `run.sh` invocation
Records every run, including failed ones. Helps diagnose repeated failures.

| Column | Type | Meaning |
|--------|------|---------|
| `id` | INTEGER PRIMARY KEY | Autoincrement |
| `date` | DATE | Run date |
| `start_time` | TIMESTAMP | When `run.sh` started |
| `end_time` | TIMESTAMP | When it ended (NULL if still running) |
| `outcome` | TEXT | `started` (incomplete), `ok` (success), or `failed` |
| `failed_stage` | TEXT | Stage that failed (`fetch`, `prefilter`, `rank`, `write`, `publish`, or NULL if ok) |
| `exit_code` | INTEGER | Exit code of the failed stage (NULL if ok) |
| `code_commit` | TEXT | Git commit SHA of the code that ran |
| `tokens_in` | INTEGER | Tokens used in this attempt (cumulative across all LLM calls) |
| `tokens_out` | INTEGER | Output tokens |
| `notes` | TEXT | Notes on the attempt (error details, model info, etc.) |

#### `topics_covered` — Reserved for cross-day dedup
Schema exists but **not currently used** (issue #4). Reserved for future topic-level dedup so "5th take on DSPy 2.5" can be down-weighted if already featured.

| Column | Type |
|--------|------|
| `topic_slug` | TEXT PRIMARY KEY |
| `last_featured_date` | DATE |
| `edition` | TEXT |

---

## Practical Scenarios

### Scenario 1: "Why didn't this paper rank higher?"

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT id, title, score, section, status, one_line_why FROM items
WHERE title LIKE '%Lato%'  -- partial search
LIMIT 1;
SQL
```

This shows the score the ranker assigned and the section. If `status` is `candidate`, the paper never got scored (expired or pre-rank capped). If `score` is NULL, investigate:
- Was the ranker LLM called for this section that day?
- Did the ranker fail or timeout?
- Check `logs/<slug>/run-<date>.log` for ranker output.

### Scenario 2: "Item appeared twice in different issues"

Cross-day dedup prevents re-featuring, but an appendix-only item can re-enter once:

```bash
-- Find all entries for a URL across all statuses
sqlite3 editions/agents/state.db << 'SQL'
SELECT date, status, appearances FROM (
  SELECT published_at as date, status, appearances FROM items
  WHERE canonical_url = 'https://arxiv.org/abs/2403.00100'
)
ORDER BY date DESC;
SQL
```

If you see the same `id` appear in `runs` from two different dates with `featured` status, that's a bug — report it. If it's `featured` then `published` on the same date, that's correct (status transition). If it's `appendix` then `featured` on day 2, that's the appendix-retry mechanism working (rare, needs `appearances < limit`).

### Scenario 3: "Prefilter was too tight yesterday"

Check the breakdown of items through each gate:

```bash
# Read from the logs, not the DB (prefilter writes debug artifacts)
tail -100 logs/agents/run-$(date +%Y-%m-%d).log | grep "prefilter:"
```

Logs show: `prefilter: rss 45 → 30 (recency) → 28 (keyword) → 25 (dedup) → 20 final`

If the keyword gate is cutting too much, check `editions/agents/edition.yaml` `keywords` list and consider adding domain-specific terms.

### Scenario 4: "Cost is creeping up"

Track cost trends and token usage:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT date, items_featured, tokens_in, tokens_out, 
       (tokens_in + tokens_out) / items_featured as tokens_per_featured
FROM runs
WHERE date >= date('now', '-30 days')
ORDER BY date DESC;
SQL
```

Rising tokens-per-featured suggests the ranker is processing more candidates or the writer is more verbose. Check the prefilter — if candidates aren't filtering well, the ranker works harder.

---

## Tips for Debugging

1. **Always check `run_attempts` first** — it tells you what failed before you dig into SQL.
   ```bash
   sqlite3 editions/agents/state.db \
     "SELECT outcome, failed_stage, exit_code FROM run_attempts \
      WHERE date = date('now') ORDER BY start_time DESC LIMIT 1;"
   ```

2. **Read the logs, not the DB** — ranker/writer output, source counts, and error details are in logs; the DB only records final state.

3. **`status = 'candidate'` doesn't mean "low quality"** — it means "not yet ranked." Papers stay `candidate` if they lost the cap on a heavy day (they'll re-compete tomorrow).

4. **Watch the `appearances` column** — tells you if an item is re-entering the pool or stuck in the appendix.

5. **`tokens_in` + `tokens_out` in `runs` is cumulative** — if a single run made the ranker call three times (papers, news, blogs) plus the writer once, you're looking at total tokens across all four calls.

---

## See Also

- [Metrics README](metrics/README.md) — public metrics definition
- [Debugging Guide](debugging-guide.md) — stage-by-stage troubleshooting
- [SPEC.md § Data Model](../SPEC.md#data-model-sqlite) — authoritative schema and design rationale
- [Multi-Edition Operations](multi-edition-operations.md) — querying multiple `state.db` files
