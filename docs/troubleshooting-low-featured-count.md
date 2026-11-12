# Troubleshooting: Missing or Low Featured Count

When an issue publishes with fewer featured items than expected, or when a section has zero featured items, this guide walks you through diagnosis and recovery.

---

## Quick Diagnosis

**When did this start?** Check recent run logs:

```bash
# Last 3 runs
sqlite3 .worktrees/content/state.db \
  "SELECT date, items_featured, items_papers, items_news, items_blogs \
   FROM runs ORDER BY date DESC LIMIT 3;"
```

If featured counts are consistently low (e.g., 5 instead of 12), you have a chronic issue. If it's a one-off spike, check that day's logs.

---

## Stage-by-Stage Diagnosis

### 1. Did fetch complete?

```bash
tail -30 logs/agents/run-$(date +%Y-%m-%d).log | grep "── fetch ──"
```

Look for `seen=... new=...`. If `seen=0` (no items fetched at all), one or more sources failed or returned empty.

**Fix:**

```bash
# Check fetch details
tail -50 logs/agents/run-$(date +%Y-%m-%d).log | grep -A 10 "── fetch ──"

# Look for errors like:
#   rss/latent_space: collector failed: HTTP 403
#   arxiv/agents: collector failed: ... 429

# If a source failed, check if it's consistently dead
uv run scripts/dead_sources.py

# Retry fetch + re-run downstream
./run.sh --refetch --edition agents
```

### 2. Did prefilter eliminate too many?

```bash
tail -50 logs/agents/run-$(date +%Y-%m-%d).log | grep "── prefilter ──"
```

Look for the counts: `450 fetched → 390 recency → 125 keyword → 120 dedup → 120 candidates`.

If candidates dropped sharply (e.g., `450 fetched → 50 keyword → 50 dedup`), the keyword gate is too tight.

**Check the gate:**

```bash
sqlite3 .worktrees/content/state.db \
  "SELECT DISTINCT keywords FROM editions WHERE slug = 'agents';"
```

Or read `editions/agents/edition.yaml` directly:

```bash
grep -A 10 "keywords:" editions/agents/edition.yaml
```

**If keywords are too tight:**

1. Add new patterns to `keywords` in `editions/agents/edition.yaml`
2. Run `uv run scripts/check_edition.py agents` to verify the gate doesn't cut too much
3. Commit and push (or re-run with `.env` changes if testing locally)

Example: if many papers on "AI agents in software development" are being gated out, add that phrase:

```yaml
keywords:
  papers: |
    (agent|agentic) AND (framework|architecture|pattern|orchestr|planning|tool-use|memory|state|dedup|eval|prompt|llm|reasoning|decision)
    OR (ai OR llm) AND (software|code|dev|review|refactor|test|debug|infra)
```

### 3. Did ranking fail silently?

```bash
tail -100 logs/agents/run-$(date +%Y-%m-%d).log | grep "── rank ──"
```

Look for:
- `papers: ranker returned 45 entries` — normal.
- `papers: content filter blocked a batch of 87; bisecting into 43 + 44` — OK, being recovered.
- `papers: ranker call failed for a batch of 87 items (HTTPError: 503); leaving unscored (appendix fallback) instead of aborting the run` — **problem**: ranker failed after retries.

**If ranker failed:**

Check the detailed log:

```bash
grep -A 5 "ranker call failed" logs/agents/run-$(date +%Y-%m-%d).log
```

Was it a timeout, rate limit, or server error? Check `logs/agents/llm-rank.log` for the full error.

**Retry:**

```bash
./run.sh --force --edition agents
```

This re-runs from prefilter → rank → write → publish, reusing today's fetched items.

### 4. Did writer fail?

```bash
tail -100 logs/agents/run-$(date +%Y-%m-%d).log | grep "── write ──"
```

If you don't see `── write ── ok`, the stage never completed. Check:

```bash
tail -50 logs/agents/run-$(date +%Y-%m-%d).log | tail -20
```

If the last line is `── rank ── ok` with no `write` line after, the writer never started (it's called after rank). This can happen if:

1. **Rank left nothing to write.** All sections got 0 ranked items.
2. **Writer hung or timed out.** Check `ps aux` for any running processes.

**Diagnose:**

```bash
# How many featured items did rank produce?
sqlite3 .worktrees/content/state.db \
  "SELECT COUNT(*) FROM items WHERE status = 'featured' AND published_at = '$(date +%Y-%m-%d)';"

# Is there an issue file?
ls -la .worktrees/content/site/src/content/issues/agents/$(date +%Y-%m-%d).md

# Check run_attempts
sqlite3 .worktrees/content/state.db \
  "SELECT outcome, failed_stage FROM run_attempts WHERE date = '$(date +%Y-%m-%d)' ORDER BY start_time DESC LIMIT 1;"
```

**If writer hung:**

```bash
# Kill it
pkill -f "rank.py\|write.py"

# Re-run
./run.sh --force --edition agents
```

### 5. Items are featured but the issue has zero items

This should not happen. If it does:

```bash
# Check what's in the issue file
head -50 .worktrees/content/site/src/content/issues/agents/$(date +%Y-%m-%d).md

# Check if publish succeeded
sqlite3 .worktrees/content/state.db \
  "SELECT items_featured FROM runs WHERE date = '$(date +%Y-%m-%d)';"
```

If `runs` says items_featured > 0 but the file is empty, it's a data loss bug. Create an issue with the output above.

---

## Common Scenarios

### Scenario: Issue publishes but only 4 featured items (target is 12–17)

**Likely cause:** One or two sections failed silently.

```bash
tail -100 logs/agents/run-$(date +%Y-%m-%d).log | grep -E "papers:|news:|blogs:"
```

Check the counts:
- `papers: ranker returned 0 entries` — papers section got nothing. Check fetch → prefilter.
- `papers: ranker call failed` — ranked failed for papers. Check endpoint status and retry.
- `papers: content filter blocked...` — normal recovery; should not cause zero items unless every subdivision was filtered.

### Scenario: Issue has only papers, zero news/blogs

The gate or sources for news/blogs are not working.

```bash
# Check if any news/blog items fetched
sqlite3 .worktrees/content/state.db \
  "SELECT section, COUNT(*) FROM items WHERE fetched_at >= datetime('now', '-1 day') GROUP BY section;"

# Check prefilter counts per section
tail -50 logs/agents/run-$(date +%Y-%m-%d).log | grep "prefilter:"
```

If news/blogs don't appear in the prefilter output, sources might be returning zero items or they are all gated.

**Check news/blogs sources:**

```bash
uv run scripts/check_edition.py agents 2>&1 | grep -E "rss:|reddit:|html:"
```

If a source shows `0 fetched`, it is dead. Check `uv run scripts/dead_sources.py` and either revive it or remove it from `sources.yaml`.

### Scenario: All sections return items but featured count is 8 (low)

The thresholds might be set too high.

```bash
# Check what scores were assigned
sqlite3 .worktrees/content/state.db \
  "SELECT section, COUNT(*) FROM items WHERE status IN ('featured', 'appendix') AND published_at = '$(date +%Y-%m-%d)' GROUP BY section;"

sqlite3 .worktrees/content/state.db \
  "SELECT section, MIN(score), MAX(score) FROM items WHERE status = 'featured' AND published_at = '$(date +%Y-%m-%d)' GROUP BY section;"
```

If featured items have a narrow score range (e.g., all 9–10), the thresholds are too high. Read the thresholds:

```bash
grep -E "featured_min|appendix_min|featured_cap" editions/agents/edition.yaml
```

To lower the threshold and see more featured items:

```yaml
featured_min: 7    # was 8; see more items at 7–8
appendix_min: 5    # was 6
featured_cap: 17   # was 15; allow more if there are good ones
```

Commit and re-run: `./run.sh --force --edition agents`.

---

## Before Escalating

1. **Confirm the issue is reproducible.** Re-run: `./run.sh --force --edition agents`. Does it happen again?
2. **Check your edition's config.** Review `editions/agents/edition.yaml` (keywords, thresholds).
3. **Check the endpoint.** Is your LLM endpoint slow, rate-limiting, or down? Test with curl or a simple OpenAI call.
4. **Check logs.** `tail logs/agents/llm-rank.log` and `logs/agents/llm-write.log` for LLM-specific errors.
5. **Check sources.** Are they returning items? Run `uv run scripts/check_edition.py agents`.

If all checks pass but the issue persists, create an issue with:

```bash
# Attach these
sqlite3 .worktrees/content/state.db "SELECT date, items_featured, items_papers, items_news, items_blogs FROM runs ORDER BY date DESC LIMIT 3;"
tail -100 logs/agents/run-$(date +%Y-%m-%d).log
```

---

## Further Reading

- [Debugging and Diagnostics Playbook](debugging-guide.md) — full troubleshooting guide
- [LLM Error Handling Guide](llm-error-handling-guide.md) — ranker/writer failures and retries
- [Edition Tuning Guide](edition-tuning-guide.md) — adjusting keywords, sources, thresholds
- [Ranking Rubric](ranking-rubric.md) — understanding the scoring system
