# Operations Runbook: Quick Reference

Fast lookup for common operational tasks. For detailed explanations, see [operations-and-maintenance.md](operations-and-maintenance.md), [debugging-guide.md](debugging-guide.md), and [multi-edition-operations.md](multi-edition-operations.md).

---

## Daily Checks

```bash
# 1. Did all editions finish?
cat logs/run-$(date +%Y-%m-%d).log          # look for summary block

# 2. Any failed editions?
grep -L '\[run\] DONE' logs/*/run-$(date +%Y-%m-%d).log

# 3. Check published items count
sqlite3 .worktrees/content/editions/agents/state.db \
  "SELECT date, items_featured, items_papers, items_news, items_blogs \
   FROM runs ORDER BY date DESC LIMIT 3"

# 4. Quick health check (all editions)
./scripts/watchdog.sh --check
```

**Healthy indicators:**
- Summary shows `failed=0`
- No grep output (all logs end in `DONE`)
- `items_featured` in expected range (typically 12–17)
- `watchdog.sh --check` shows `EDITION: ... ALREADY_RAN`

---

## Common Scenarios

### Scenario: One edition failed, others succeeded

```bash
# 1. Identify which edition failed
grep 'failed' logs/run-$(date +%Y-%m-%d).log

# 2. See the full error
tail -100 logs/<slug>/run-$(date +%Y-%m-%d).log

# 3. Re-run just that edition (idempotent)
./run.sh --edition <slug>

# 4. Check if it succeeded
tail -5 logs/<slug>/run-$(date +%Y-%m-%d).log
```

### Scenario: All editions failed at the same stage

```bash
# 1. See what stage failed
grep '\[run\]' logs/run-$(date +%Y-%m-%d).log | tail -1

# 2. Check credentials (.env exists and is readable?)
ls -la .env ~/.config/agent-newsletter/env 2>&1

# 3. Check LLM endpoint is up (if rank/write failed)
curl -s https://api.openai.com/v1/health 2>&1 | head -2

# 4. Manual re-run with trace
LLM_BASE_URL=<your-url> ./run.sh -v 2>&1 | tee debug.log
```

### Scenario: Too few items featured today

```bash
# 1. Check how many got fetched
tail -10 logs/agents/run-$(date +%Y-%m-%d).log | grep "fetch:"

# 2. Check how many passed prefilter
grep "prefilter:" logs/agents/run-$(date +%Y-%m-%d).log

# 3. Check ranker scores
sqlite3 .worktrees/content/editions/agents/state.db \
  "SELECT COUNT(*), score FROM items \
   WHERE fetched_at > date('now') AND section='papers' \
   GROUP BY score ORDER BY score DESC"

# 4. If keyword gate is too tight
# → See [prefilter-gates-guide.md](prefilter-gates-guide.md)
# → Adjust keywords in editions/agents/edition.yaml
# → Re-run with ./run.sh --force
```

### Scenario: Too many items, unexpected mix of sections

```bash
# Check the counts
sqlite3 .worktrees/content/editions/agents/state.db \
  "SELECT items_papers, items_news, items_blogs FROM runs \
   WHERE date = (SELECT max(date) FROM runs)"

# Adjust section thresholds in edition.yaml
# → featured_min, appendix_min, cap per section
# → See [edition-yaml-reference.md](edition-yaml-reference.md)

# Re-rank from scratch
./run.sh --force
```

### Scenario: A feed source stopped working

```bash
# 1. Check what it fetched last
sqlite3 .worktrees/content/editions/agents/state.db \
  "SELECT source, COUNT(*), MAX(fetched_at) FROM items \
   WHERE source LIKE 'rss:%' GROUP BY source ORDER BY fetched_at DESC"

# 2. Manually test the feed
curl -s https://example.com/feed.xml | head -20

# 3. If it's down, comment it out in sources.yaml or remove it
# → See [adding-feed-sources-guide.md](adding-feed-sources-guide.md)

# 4. Run check_edition to verify syntax
uv run scripts/check_edition.py agents
```

### Scenario: Need to regenerate yesterday's issue (backfill)

```bash
# 1. Ensure content worktree exists
git worktree add .worktrees/content content 2>/dev/null || true

# 2. Dry-run (shows what would be generated, doesn't modify DB)
uv run scripts/backfill.py --date 2026-11-01

# 3. Actually backfill (updates state.db with published items)
uv run scripts/backfill.py --date 2026-11-01 --apply-published

# See [CONTRIBUTING.md § Operational scripts](../CONTRIBUTING.md#operational-scripts--backfill-and-replay)
```

### Scenario: Need to replay writer (test prompt changes)

```bash
# 1. Edit prompts/write.md or editions/agents/prompts/write.*.md

# 2. Ensure content worktree and .env are set up
git worktree add .worktrees/content content 2>/dev/null || true
cp .env.template .env && $EDITOR .env

# 3. Replay against a past date
uv run scripts/replay_writer.py 2026-11-01

# 4. Check output
cat logs/theme-replay-2026-11-01.json | jq '.featured[:2]'

# See [CONTRIBUTING.md § Operational scripts](../CONTRIBUTING.md#operational-scripts--backfill-and-replay)
```

### Scenario: macOS notification not firing (watchdog.sh)

```bash
# 1. Check if the run really did stall (>36 hours without success)
git log --oneline content 2>/dev/null | head -5

# 2. Force a watchdog re-check
rm -f logs/watchdog-last-fire
./watchdog.sh

# 3. If watchdog.sh still not firing, check osascript
osascript -e 'display notification "test"'

# 4. Check watchdog output
tail -20 logs/watchdog.out

# See [README.md § Troubleshooting](../README.md#troubleshooting-a-stalled-run)
```

### Scenario: Remove all state and start fresh

```bash
# WARNING: This deletes local history. Only do this if moving machines or reset needed.

# 1. Backup current state (optional)
cp .worktrees/content/editions/agents/state.db state-backup.db

# 2. Remove content worktree and start over
rm -rf .worktrees/content
git worktree prune
git worktree add .worktrees/content content

# 3. Next run will create a fresh state.db
./run.sh

# 4. If you need to restore specific data from backup:
sqlite3 state-backup.db "SELECT ... FROM items" >> state.db
```

---

## Monitoring: Weekly Checks

```bash
# Trends over the last 14 days
DB=.worktrees/content/editions/agents/state.db
sqlite3 -header "$DB" "
  SELECT date, items_fetched, items_candidate, items_featured,
         items_papers, items_news, items_blogs,
         tokens_in, tokens_out, duration_seconds
  FROM runs
  WHERE date >= date('now', '-14 days')
  ORDER BY date DESC"

# What's healthy?
# - items_featured: stable for the edition (compare to its own baseline)
# - items_papers/news/blogs: matches the cap values from edition.yaml
# - tokens_in/out: growth is OK if candidate pool grew; collapse suggests outage
# - duration_seconds: stable; growth suggests slower model or more candidates
```

---

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `LLM_BASE_URL` | OpenAI-compatible endpoint | `https://api.openai.com/v1` |
| `LLM_API_KEY` | Bearer token | (redacted) |
| `RANKER_MODEL` | Model for ranking | `gpt-4-turbo` |
| `WRITER_MODEL` | Model for writing | `gpt-4` |
| `RANKER_TIMEOUT_S` | Max seconds per ranker call | `1800` (30 min) |
| `WRITER_TIMEOUT_S` | Max seconds per writer call | `1200` (20 min) |
| `EDITION` | Which edition to run (optional) | `agents` |
| `STALE_HOURS` | Watchdog alert threshold | `36` |
| `CONTENT_ROOT` | Worktree path (set by run.sh) | `.worktrees/content` |

**Location:** `.env` (repo-local, gitignored) or `~/.config/agent-newsletter/env` (machine-wide; `.env` wins).

---

## Debugging Commands

```bash
# See every fetch source and item count
tail -20 logs/agents/run-$(date +%Y-%m-%d).log | grep -E "fetch:|source:"

# Ranker hung? See if process is running
ps aux | grep rank.py

# Writer hung? See if process is running
ps aux | grep write.py

# Find items matching a title
sqlite3 .worktrees/content/editions/agents/state.db \
  "SELECT id, title, score, status FROM items WHERE title LIKE '%keyword%' LIMIT 5"

# See why an item was dropped
sqlite3 .worktrees/content/editions/agents/state.db \
  "SELECT id, title, status, first_seen_date FROM items WHERE id='<sha256>' LIMIT 1"

# List all editions and their status
sqlite3 .worktrees/content/editions/agents/state.db \
  "SELECT * FROM meta WHERE key IN ('status', 'order')"
```

---

## When to escalate

- **LLM endpoint is down** — check provider status page; not a repo problem
- **Source feed is broken** — temporarily disable in sources.yaml; report to source owner
- **Same failure stage for 3+ days** — check for code bugs; file an issue
- **`flock` command not found** (macOS) — install via Homebrew; `run.sh` exits 0 but does nothing
- **Database corruption** — rare, but if `sqlite3` errors on `state.db`, move it to backup and let the next run create a fresh one

---

## Reference

For detailed explanations and troubleshooting:
- [operations-and-maintenance.md](operations-and-maintenance.md) — where things live, daily checks, trends
- [debugging-guide.md](debugging-guide.md) — stage-by-stage diagnosis, common errors
- [multi-edition-operations.md](multi-edition-operations.md) — managing multiple editions
- [README.md § Troubleshooting](../README.md#troubleshooting-a-stalled-run) — extended runbook

