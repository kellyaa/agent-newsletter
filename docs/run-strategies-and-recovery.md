# Run Strategies and Recovery Scenarios

This guide covers when to use `--force` vs `--refetch`, how to recover from incomplete runs, and strategies for different failure scenarios.

---

## Pipeline Idempotency: Why Re-running is Safe

Every stage in the pipeline is **idempotent**: running it twice on the same day with the same data produces the same result.

- `fetch.py` uses `INSERT OR IGNORE` on the id, so re-fetching from the same sources adds no duplicates.
- `prefilter.py` reads `state.db` and writes to a temp file; re-running overwrites it.
- `rank.py` reads candidates and writes scores back; re-running replaces scores.
- `write.py` generates the issue file; re-running overwrites it.
- `publish.py` marks items as published and commits; re-running is a no-op if they are already published.

**Consequence:** You can safely re-run `run.sh` at any point if something goes wrong. Completed stages are skipped; incomplete or failed stages are retried.

---

## When to Use Each Flag

### `./run.sh` (default)

**Use:** Daily scheduled runs, or manual runs after short breaks.

**Behavior:**
- Runs every `draft` and `live` edition (skips `paused`).
- Skips an edition if its latest attempt today was `ok`.
- Skips fetch if it already completed today.
- Cheaper than `--force` (no re-ranking if ranking already succeeded).

**Cost:** Near zero if all editions succeeded today; fetches + ranks/writes again if they failed.

**Example:** After a transient network glitch:

```bash
./run.sh
```

The run will skip to the failed stage and retry.

---

### `./run.sh --failed`

**Use:** Re-run only the editions that failed today; useful in a watchdog or after fixing a config issue.

**Behavior:**
- Queries `run_attempts` for editions whose latest attempt today has outcome `failed` or `started`.
- Runs only those editions; skips successful ones.
- Exits 0 ("nothing to re-run") if all are ok.

**Cost:** Depends on which stage failed for each edition. Normally just re-ranks and writes.

**Example:** One edition failed, you fixed `edition.yaml`, now re-run it:

```bash
./run.sh --failed
```

Or combine with others:

```bash
./run.sh --failed  # re-run agents and engineering-with-ai if they failed
```

---

### `./run.sh --edition X` (repeatable)

**Use:** Manual runs of specific editions, testing changes, bypassing the schedule.

**Behavior:**
- Runs only the specified edition(s); `-–edition` is repeatable.
- Even `paused` editions are allowed (for manual testing).
- Does not skip completed stages; starts from the beginning of that day's pipeline.

**Cost:** Full pipeline: fetch + prefilter + rank + write + publish. ~1–2 min per edition on a fast LLM endpoint.

**Example:** Test `engineering-with-ai` edition:

```bash
./run.sh --edition engineering-with-ai
```

Or run both:

```bash
./run.sh --edition agents --edition engineering-with-ai
```

---

### `./run.sh --force`

**Use:** Re-run today's pipeline downstream of fetch (e.g., after changing keywords or thresholds, or to re-rank after an LLM model switch).

**Behavior:**
- Per selected edition(s): delete today's prefilter/rank/write state, keeping fetched items.
- Re-runs prefilter → rank → write → publish.
- Fetch is skipped; fetched items are reused.

**Cost:** No network fetch; just re-ranking and writing. ~30 s to 3 min depending on item count and LLM latency.

**arXiv safety:** Safe; does not re-fetch from arXiv (which rate-limits aggressively).

**Example:** You tuned `keywords` in `edition.yaml`:

```bash
./run.sh --force --edition agents
```

Or re-run everything with a new `WRITER_MODEL`:

```bash
WRITER_MODEL=gpt-4o ./run.sh --force
```

---

### `./run.sh --refetch --edition X`

**Use:** Re-fetch from all sources for a specific edition (use sparingly).

**Behavior:**
- Requires `--edition` (prevents accidentally re-pulling arXiv for all editions).
- Deletes today's fetched items; re-fetches from all sources.
- Re-runs prefilter → rank → write → publish with new fetch.

**Cost:** Full network fetch + compute. arXiv is rate-limited (wait ~30s between runs if re-fetching multiple times).

**Example:** arXiv had a 429 error yesterday; retry today:

```bash
./run.sh --refetch --edition agents
```

**Do NOT use:** Back-to-back on the same edition within a few minutes (arXiv will rate-limit you).

---

## Failure Scenarios and Recovery

### Scenario 1: Fetch Failed (HTTP 500, Network Error)

**Symptoms:**

```
── fetch ── start
seen=0 new=0 errors=5
── fetch ── FAILED
```

All or most sources errored out; no items were fetched.

**Recovery:**

```bash
# Wait a few minutes for the endpoint to recover
sleep 300

# Re-run (fetch will retry)
./run.sh
```

If a specific source is repeatedly failing:

```bash
# List dead sources
uv run scripts/dead_sources.py

# Remove the source from editions/agents/sources.yaml
# Push the change
git add -A && git commit -s -m "remove dead source"
git push

# Re-run
./run.sh --edition agents
```

---

### Scenario 2: Prefilter Eliminated Everything (Keywords Too Tight)

**Symptoms:**

```
── prefilter ── start
prefilter: 500 fetched → 500 recency → 10 keyword → 10 dedup → 10 candidates
── prefilter ── ok (but rank then returns 0 items)
```

The keyword gate is too strict.

**Recovery:**

1. Edit `keywords` in `editions/agents/edition.yaml` to be more permissive.
2. Test without running the full pipeline:
   ```bash
   uv run scripts/check_edition.py agents
   ```
   Look for the prefilter counts; if they look better, proceed.
3. Re-run:
   ```bash
   ./run.sh --force --edition agents
   ```

---

### Scenario 3: Ranker Timed Out or Hit Rate Limit

**Symptoms:**

```
── rank ── start
papers: ranker call failed for a batch of 87 items (timeout); leaving unscored (appendix fallback)
news: ranker returned 15 entries
blogs: ranker returned 8 entries
── rank ── ok
```

Papers section got no ranked items.

**Recovery (if temporary):**

```bash
# Wait a few minutes
sleep 300

# Re-run with more generous timeout
RANKER_TIMEOUT_S=3600 ./run.sh --force --edition agents
```

**Recovery (if consistent):**

1. Switch to a faster/cheaper ranker model:
   ```bash
   RANKER_MODEL=gpt-4o-mini ./run.sh --force --edition agents
   ```
2. Or lower the timeout but increase retries (this is not configurable today; contact the maintainers).
3. Or increase `RANKER_TIMEOUT_S` in `.env` permanently:
   ```bash
   echo 'RANKER_TIMEOUT_S=3600' >> .env
   ```

---

### Scenario 4: Writer Failed (No Issue Produced)

**Symptoms:**

```
── write ── start
writer: writer call failed after 3 retries (HTTP 429: rate limit)
── write ── FAILED
```

The issue file was not written; the run is marked `failed`.

**Recovery:**

```bash
# Wait (if rate limited)
sleep 600

# Re-run just write + publish (using --force)
./run.sh --force --edition agents
```

**If it keeps failing:**

Check your LLM endpoint's rate limit and quota. If you're on a pay-as-you-go plan, verify credit is available.

---

### Scenario 5: Partial Publish (Some Editions Succeeded, One Failed)

**Symptoms:**

```
LOG: agents completed: ok
LOG: engineering-with-ai completed: failed at write
```

`agents` published; `engineering-with-ai` did not.

**Recovery:**

```bash
# Re-run only the failed edition
./run.sh --failed

# Or run it manually
./run.sh --edition engineering-with-ai
```

---

### Scenario 6: Run Hung (Process Stuck for Hours)

**Symptoms:**

```bash
ps aux | grep run.sh
# Shows a process that has been running for 12+ hours
```

Likely a stuck LLM call or network hang.

**Recovery:**

```bash
# Kill the hung process
pkill -f "$(pwd)/run.sh"

# Wait a moment
sleep 5

# Re-run (will start from where it left off or re-run the stage)
./run.sh
```

If it hangs again on the same stage, increase the timeout:

```bash
RANKER_TIMEOUT_S=3600 WRITER_TIMEOUT_S=1800 ./run.sh --force --edition agents
```

---

## Advanced: Backfilling a Missed Day

If the pipeline didn't run on a day (e.g., the server was off), you can reconstruct that issue from the candidate pool.

```bash
# Reconstruct (dry-run: doesn't modify state.db)
uv run scripts/backfill.py --date 2026-06-10

# Reconstruct and publish (modifies state.db)
uv run scripts/backfill.py --date 2026-06-10 --apply-published

# Commit and push
git add -A && git commit -s -m "backfill: June 10"
git push origin content
```

See [CONTRIBUTING.md § scripts/backfill.py](../CONTRIBUTING.md#scriptsbackfillpy--reconstruct-a-missed-days-issue) for details.

---

## Advanced: Replaying the Writer

To test prompt changes without re-fetching and re-ranking:

```bash
# Re-run writer against published items from 2026-06-10
uv run scripts/replay_writer.py 2026-06-10

# Output goes to logs/theme-replay-2026-06-10.json
cat logs/theme-replay-2026-06-10.json | jq .
```

See [CONTRIBUTING.md § scripts/replay_writer.py](../CONTRIBUTING.md#scriptsreplay_writerpy--replay-writer-against-a-past-issue) for details.

---

## Monitoring Recovery

After any recovery, monitor the next few runs:

```bash
# Check status
sqlite3 .worktrees/content/state.db \
  "SELECT date, outcome, failed_stage FROM run_attempts ORDER BY date DESC LIMIT 3;"

# Watch the logs
tail -f logs/run-$(date +%Y-%m-%d).log
```

If the problem recurs in the next 2–3 days, it is likely not transient. Check your config, LLM endpoint status, and feed sources.

---

## Further Reading

- [Debugging and Diagnostics Playbook](debugging-guide.md) — full troubleshooting workflow
- [LLM Error Handling Guide](llm-error-handling-guide.md) — understanding retries and ContentFilterError
- [Command Reference](command-reference.md) — all operational commands
