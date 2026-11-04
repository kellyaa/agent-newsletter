# SQL Export and Data Analysis Guide

The newsletter's `state.db` SQLite database contains a complete history of items fetched, ranked, and published. This guide covers how to export data for external analysis, build custom reports, and audit the pipeline's decisions.

## Quick Start: Export Patterns

### CSV Export by Date Range

Export featured items to CSV for analysis:

```bash
EDITION=agents
sqlite3 editions/agents/state.db << 'SQL' | head -100
.headers on
.mode csv
.output featured_items.csv
SELECT 
  published_at as date,
  source,
  title,
  author,
  score,
  section,
  tags
FROM items
WHERE status = 'published'
  AND published_at >= '2026-10-01'
  AND published_at < '2026-11-01'
ORDER BY published_at DESC, score DESC;
.output stdout
SQL
```

### JSON Export for API Integration

Export run metrics in JSON for dashboards or monitoring systems:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT json_object(
  'date', date,
  'featured', items_featured,
  'papers', items_papers,
  'news', items_news,
  'blogs', items_blogs,
  'tokens_in', tokens_in,
  'tokens_out', tokens_out,
  'duration_s', duration_seconds,
  'tokens_per_featured', 
    ROUND(CAST(tokens_in + tokens_out AS REAL) / items_featured, 1)
) as metrics
FROM runs
WHERE date >= date('now', '-30 days')
ORDER BY date DESC;
SQL
```

---

## Common Analysis Scenarios

### Source Performance Analysis

Track which sources consistently produce high-scoring items:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT 
  source,
  COUNT(*) as fetched,
  SUM(CASE WHEN status IN ('featured', 'published') THEN 1 ELSE 0 END) as featured,
  ROUND(AVG(score), 1) as avg_score,
  MAX(score) as max_score,
  ROUND(100.0 * SUM(CASE WHEN status IN ('featured', 'published') THEN 1 ELSE 0 END) / COUNT(*), 1) as featured_pct
FROM items
WHERE fetched_at >= date('now', '-60 days')
GROUP BY source
ORDER BY featured DESC
LIMIT 20;
SQL
```

**Interpretation:**
- **`featured_pct`** > 30% = high-quality source (trusted for keyword bypass)
- **`avg_score`** < 4 = source may need keyword tuning or recency override
- **`fetched`** = 0 = source is broken or temporarily down

### Item Deduplication Audit

Examine cross-day re-appearances and the dedup mechanism:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT 
  canonical_url,
  COUNT(DISTINCT published_at) as days_appeared,
  COUNT(*) as total_appearances,
  MAX(CASE WHEN status IN ('featured', 'published') THEN published_at END) as last_featured_date,
  GROUP_CONCAT(DISTINCT status) as statuses
FROM items
WHERE appearances > 1
GROUP BY canonical_url
HAVING COUNT(DISTINCT published_at) > 1
ORDER BY COUNT(*) DESC
LIMIT 20;
SQL
```

**Key fields:**
- **`appearances`** > 1 = item re-entered the candidate pool on a later day
- **`days_appeared`** = how many separate dates this item appeared
- **`statuses`** = lifecycle states it went through (e.g., "candidate,featured,published")

### LLM Cost Tracking

Monitor per-model token usage and identify cost trends:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT 
  date,
  json_extract(notes, '$.ranker.model') as ranker_model,
  json_extract(notes, '$.writer.model') as writer_model,
  tokens_in,
  tokens_out,
  tokens_in + tokens_out as total_tokens,
  items_featured,
  ROUND(CAST(tokens_in + tokens_out AS REAL) / items_featured, 0) as tokens_per_item
FROM runs
WHERE date >= date('now', '-90 days')
ORDER BY date DESC;
SQL
```

**Analysis tips:**
- **`tokens_per_item`** trend upward? = ranker processing more candidates (tighten keyword gate or check recency)
- Model changes visible in `ranker_model` / `writer_model` columns
- Use `tokens_in` + `tokens_out` × your provider's per-token price to calculate actual cost (since `cost_usd` is not yet populated)

### Editorial Impact: Section Balance

Track how featured items are distributed across sections over time:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT 
  date,
  items_papers,
  items_news,
  items_blogs,
  items_featured,
  ROUND(100.0 * items_papers / items_featured, 0) as papers_pct,
  ROUND(100.0 * items_news / items_featured, 0) as news_pct,
  ROUND(100.0 * items_blogs / items_featured, 0) as blogs_pct
FROM runs
WHERE date >= date('now', '-60 days')
ORDER BY date DESC;
SQL
```

Check if section balance matches your edition's thresholds (defined in `editions/<slug>/edition.yaml`).

---

## Advanced Exports

### Export for Audit Trail (Compliance)

Create a detailed audit log of all featured items with ranker rationale:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT 
  published_at as publication_date,
  source,
  title,
  url,
  score,
  section,
  why as ranker_rationale,
  tags as keywords,
  (SELECT start_time FROM run_attempts 
   WHERE date = items.published_at 
   ORDER BY start_time DESC LIMIT 1) as ranked_at
FROM items
WHERE status IN ('featured', 'published')
  AND published_at >= '2026-01-01'
ORDER BY published_at DESC;
SQL
```

Export to CSV (add `.mode csv` before the SELECT) for archival or regulatory review.

### Batch Export: All Items and Their Scores

Export the complete dataset for offline analysis:

```bash
sqlite3 editions/agents/state.db << 'SQL'
.headers on
.mode csv
.output full_export.csv

SELECT * FROM items
WHERE fetched_at >= date('now', '-180 days')
ORDER BY fetched_at DESC;

.output stdout
SQL

# Verify export size
wc -l full_export.csv
```

This is useful for:
- Training custom ranking models
- Analyzing editorial bias
- Computing per-source quality metrics
- Sharing anonymized data with collaborators

### Performance Trending Report

Create a weekly/monthly summary of pipeline performance:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT 
  strftime('%Y-W%W', date) as week,
  COUNT(*) as runs,
  ROUND(AVG(items_featured), 1) as avg_featured,
  ROUND(AVG(tokens_in + tokens_out), 0) as avg_tokens,
  ROUND(AVG(duration_seconds), 0) as avg_duration_s,
  ROUND(AVG(CAST(tokens_in + tokens_out AS REAL) / items_featured), 0) as avg_tokens_per_item
FROM runs
WHERE date >= date('now', '-90 days')
GROUP BY week
ORDER BY week DESC;
SQL
```

---

## Piping Data to External Tools

### Export to Pandas (Python)

Read state.db directly into Python for analysis:

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("editions/agents/state.db")

# Read items as DataFrame
items = pd.read_sql_query("""
    SELECT published_at, source, score, section, status
    FROM items
    WHERE status IN ('featured', 'published')
    ORDER BY published_at DESC
""", conn)

# Analyze by section
section_stats = items.groupby('section')['score'].agg(['count', 'mean', 'std'])
print(section_stats)

conn.close()
```

### Real-time Monitoring Script

Watch `run_attempts` table for failures in real-time:

```bash
#!/bin/bash
EDITION=agents
DB="editions/$EDITION/state.db"

watch -n 60 sqlite3 "$DB" << 'SQL'
SELECT outcome, failed_stage, COUNT(*) as attempts
FROM run_attempts
WHERE date = date('now')
GROUP BY outcome, failed_stage
ORDER BY outcome DESC, failed_stage;
SQL
```

---

## Data Privacy & Retention

### What's Stored in state.db

✅ **Safe to share (public data only):**
- Item URLs and titles (from public RSS/arXiv/HN)
- Ranker scores and tags
- Metadata (fetch date, source ID, section)
- Run metrics (tokens, duration, item counts)

❌ **Never share:**
- LLM API keys (stored in `.env`, not in DB)
- Prompt context or rationales (ranker's `why` field may contain sensitive reasoning)
- `run_attempts` table may reveal internal infrastructure details

### Selective Export

Export only safe fields for sharing with analysts:

```bash
sqlite3 editions/agents/state.db << 'SQL'
SELECT 
  published_at as date,
  source,
  title,
  url,
  score,
  section,
  tags
FROM items
WHERE status IN ('featured', 'published')
ORDER BY published_at DESC;
SQL
```

Omit `why` (ranker rationale), model info, and timing data if sharing outside your team.

---

## Tips & Troubleshooting

| Issue | Solution |
|-------|----------|
| `database is locked` | Another process is writing to state.db (a run is in progress). Wait for it to finish or close the content worktree. |
| `no such table` | Table doesn't exist yet (new editions start with an empty schema). Run a pipeline stage first to initialize it. |
| `query too slow` | Add `WHERE fetched_at >= date(...)` to limit rows, or add an index: `CREATE INDEX idx_items_status ON items(status);` |
| CSV export is huge | Use date filters and status predicates to limit output. Consider exporting to Parquet instead of CSV for large datasets. |
| `NULL` scores on recent items | Items are still in `candidate` status; ranker hasn't scored them yet. Check `status` column. |

---

## See Also

- [Database Schema and Querying Guide](database-schema-guide.md) — table definitions, column meanings, and troubleshooting queries
- [Performance and Cost Guide](performance-and-cost-guide.md) — token usage, cost tracking, optimization strategies
- [metrics.md](metrics.md) — reference for every recorded metric
