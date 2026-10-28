# Public metrics

What this project measures about itself, and where the numbers come from. Satisfies the ACMM `acmm:public-metrics` criterion (`docs/metrics/`).

All pipeline metrics are recorded per edition and per day in `editions/<slug>/state.db` on the `content` branch. No metric below contains secrets or reader data.

## Pipeline metrics (`runs` table, one row per day)

| Column | Meaning |
| --- | --- |
| `items_fetched` | Items collected from all sources |
| `items_candidate` | Items that passed the prefilter and went to the ranker |
| `items_featured` | Items featured in the published issue |
| `items_papers`, `items_news`, `items_blogs` | Featured items per section |
| `duration_seconds` | Wall-clock time of the run |
| `tokens_in`, `tokens_out`, `cost_usd` | LLM usage and cost |

## Reliability metrics (`run_attempts` table, one row per `run.sh` invocation)

`outcome` (`started` / `ok` / `failed`), `failed_stage` and `exit_code` show how often and where runs fail.

## Engineering metrics

- Test count and line/branch coverage: `uv run --extra test pytest` (gate in `pyproject.toml`, currently 95%).
- Merge history and review latency: the repository's pull requests.

## Reading them

```bash
sqlite3 editions/<slug>/state.db \
  "SELECT date, items_featured, cost_usd FROM runs ORDER BY date DESC LIMIT 14"
```

(Run from a checkout of the `content` branch, e.g. `git worktree add .worktrees/content content`.)
