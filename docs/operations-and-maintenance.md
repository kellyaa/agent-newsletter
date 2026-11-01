# Operations and Maintenance Guide

The day-to-day work of keeping the newsletters running: what to check, what to do when a run fails or the output is poor, and how to recover. It ties together guides that go deeper on one topic:

- [debugging-guide.md](debugging-guide.md): stage-by-stage failure diagnosis.
- [multi-edition-operations.md](multi-edition-operations.md): run model, watchdogs, moving machines.
- [performance-and-cost-guide.md](performance-and-cost-guide.md): where time and tokens go.
- [source-reliability-matrix.md](source-reliability-matrix.md): feed failures.
- [metrics/README.md](metrics/README.md): every column of `runs` and `run_attempts`.

Statements here come from `run.sh`, `scripts/run_edition.sh`, `scripts/watchdog.sh`, `scripts/db.py` and the README. Where the repository records no number (for example run time or cost per edition) this guide says to measure it.

## 1. Where things live

| What | Where |
|---|---|
| Orchestrator log (one line per edition) | `logs/run-YYYY-MM-DD.log` |
| Full output of one edition's run; ends in `[run] DONE` on success | `logs/<slug>/run-YYYY-MM-DD.log` |
| Per-call token usage | `logs/<slug>/usage-YYYY-MM-DD.jsonl` |
| Pipeline state (`items`, `runs`, `run_attempts`, `meta`) | `.worktrees/content/editions/<slug>/state.db` |
| Published issues | `.worktrees/content/editions/<slug>/issues/YYYY-MM-DD.md` |
| Run lock | `.watchdog.lock` (an `flock` lock, not a PID file) |
| Credentials and model settings | `~/.config/agent-newsletter/env`, then `.env` (last wins) |

`state.db` and the issues are on the `content` branch, checked out by `run.sh` as the `.worktrees/content` worktree. Never commit to `content` by hand. Each edition has its own database, so every query below is run once per edition.

The examples use shell variables so you can paste them:

```bash
cd /path/to/agent-newsletter
SLUG=agents
DB=.worktrees/content/editions/$SLUG/state.db
```

## 2. Daily checks

Do these in order; stop at the first one that shows a problem and go to section 4.

1. **Did every edition finish?**

   ```bash
   cat logs/run-$(date +%F).log          # look for the "summary" block
   grep -L '\[run\] DONE' logs/*/run-$(date +%F).log   # editions whose log did NOT finish
   ```

   A healthy summary shows `failed=0`. `./scripts/watchdog.sh --check` prints one `EDITION:` line per non-paused edition and a final `STATUS:` line without running anything.

2. **Did it publish?** An edition with attempts but no `runs` row for the day did not publish.

   ```bash
   sqlite3 "$DB" "SELECT date, items_featured, items_papers, items_news, items_blogs, duration_seconds FROM runs ORDER BY date DESC LIMIT 3"
   sqlite3 "$DB" "SELECT date, attempt, outcome, failed_stage, exit_code FROM run_attempts ORDER BY date DESC, attempt DESC LIMIT 5"
   ```

   `outcome` is `ok`, `failed`, or `started`. A `started` row that never finished means the process died mid-run (power loss, killed, host restart).

3. **Read the issue.** Skim `.worktrees/content/editions/$SLUG/issues/$(date +%F).md`: does it have the expected sections, is the prose coherent, are the featured items on topic?

On macOS, a failed edition also raises a notification, and the root `watchdog.sh` (hourly) alerts if no edition has had a successful daily-run commit on `content` for `STALE_HOURS` (default 36). Because it looks across all editions, one healthy edition keeps it quiet while another fails; check each edition yourself.

## 3. Trends to watch

Look at these weekly; a single odd day is usually noise.

| Signal | Where | Healthy | If it moves |
|---|---|---|---|
| Items featured per day | `runs.items_featured` | stable for the edition (compare to its own history, not other editions) | drop: source outage or keyword gate too tight; spike: new noisy source |
| Fetched vs candidates | `runs.items_fetched`, `runs.items_candidate` | ratio roughly constant | falling fetched: a source is failing; candidates collapsing: gate or recency |
| Section balance | `runs.items_papers/news/blogs` | matches the `cap` values in `edition.yaml` | one section always empty: thresholds or source mix |
| Failure rate | `run_attempts.outcome`, `failed_stage` | rare, scattered | same `failed_stage` repeatedly: a real bug or config problem |
| Tokens | `runs.tokens_in/out` | stable | growth: larger candidate pool, longer `raw_text`, retries |
| Duration | `runs.duration_seconds` | stable for the edition | growth: slower model or more candidates |

```bash
sqlite3 -header "$DB" "
  SELECT date, items_fetched, items_candidate, items_featured,
         tokens_in, tokens_out, duration_seconds
  FROM runs WHERE date >= date('now','-14 days') ORDER BY date DESC"
```

Cost: `runs.cost_usd` is not reliably populated, so check it before relying on it. Where it is empty, price `run_attempts.tokens_by_model` (`<model>:<in>/<out>` pairs) with your provider's rates. Failed attempts are billed too, so reconcile against invoices with `run_attempts`, not only `runs`. There is no budget cap in the pipeline; set spend limits at the provider.

For all editions at once:

```bash
for db in .worktrees/content/editions/*/state.db; do
  echo "== $db"
  sqlite3 "$db" "SELECT date, items_featured, tokens_in, tokens_out FROM runs ORDER BY date DESC LIMIT 7"
done
```

## 4. Troubleshooting

### A run failed or did not happen

1. Find the stage: the summary line says `FAILED at stage=<stage> rc=<n>`, and `run_attempts.failed_stage` records it.
2. Read `logs/<slug>/run-<date>.log` around that stage. The stage-specific fixes are in [debugging-guide.md](debugging-guide.md).
3. Fix the cause, then resume. Every stage is idempotent, so this only does the missing work:

   ```bash
   ./run.sh --failed            # only editions whose latest attempt today failed or died
   ./run.sh --edition <slug>    # one edition (also works for a paused one)
   ```

   `--force --edition <slug>` discards today's rank and write output and **spends LLM tokens again**. `--refetch` also re-pulls sources (arXiv is rate limited) and requires `--edition`. Use them only when you need to.

If nothing ran at all, check whether a run is in progress or the lock is held:

```bash
pgrep -af 'run.sh|run_edition.sh|rank.py|write.py'
```

`run.sh` exits 0 with "another run holds .watchdog.lock; exiting without doing anything" when it cannot take the lock, so a scheduled job can look healthy while doing nothing. If no process is running, the lock is free, because it is released when the owning process exits. A leftover `.watchdog.lock` file is harmless; do not delete it while a run may be live. On a host without `flock` (stock macOS) the same message appears every time. See the README, "Troubleshooting".

| Symptom in the log | Likely cause | Action |
|---|---|---|
| `429` from arXiv or Reddit | rate limit | wait, then `./run.sh --failed` |
| `401` / `403` from the LLM endpoint | key expired or wrong endpoint | see "LLM credentials" in section 5 |
| rank or write runs for a very long time | model slow or hung | see "Hung LLM call" below |
| log has no `DONE`, no `FAILED` | process was killed or the host restarted | `./run.sh --failed` (the attempt shows as `started`) |

### Hung LLM call

The calls are bounded by `RANKER_TIMEOUT_S` (default 1800) and `WRITER_TIMEOUT_S` (default 1200), so a hang ends in a failed attempt without intervention. If you cannot wait, kill the stage process (`pkill -f rank.py`); the run fails at that stage and `./run.sh --failed` resumes. To allow slower models, raise the timeout in `.env`.

### Low-quality items were featured

```bash
sqlite3 -header "$DB" "
  SELECT source, score, title, why FROM items
  WHERE published_date >= date('now','-3 days') AND status = 'published'
  ORDER BY score ASC LIMIT 10"
```

For the offending item, decide which gate let it through:

- **Bypassed the keyword gate.** `SELECT keyword_gate_bypass FROM items WHERE id = '<id>'`. If it is 1, the source sets `keyword_gate_bypass: true` in `sources.yaml`; remove that if the source is not low-volume and hand-curated.
- **Ranker over-scored it.** Read `why`. If the reasoning is wrong, tighten the rubric in the edition's prompt fragments (`editions/<slug>/prompts/rank.*.md`) or raise `featured_min` for that section.
- **Source is noisy.** Remove it, or drop its volume with a narrower query.

Change one thing at a time and verify with `uv run scripts/check_edition.py <slug>` (no LLM). Tuning detail is in [edition-yaml-reference.md](edition-yaml-reference.md), [prefilter-gates-guide.md](prefilter-gates-guide.md) and [ranking-rubric.md](ranking-rubric.md).

### Good items are missing

Compare what was fetched with what survived, per source:

```bash
sqlite3 -header "$DB" "
  SELECT source, COUNT(*) AS seen,
         SUM(status IN ('dropped','expired_unjudged')) AS dropped,
         SUM(status IN ('featured','appendix','published')) AS kept
  FROM items WHERE first_seen_date >= date('now','-3 days')
  GROUP BY source ORDER BY seen DESC"
```

| Pattern | Cause | Fix |
|---|---|---|
| high `seen`, high `dropped` | keyword gate or recency too tight | broaden `keywords`; set `recency_days` on that source; `keyword_gate_bypass` only for curated sources |
| `seen` = 0 for a source | the source failed | [source-reliability-matrix.md](source-reliability-matrix.md) |
| kept but scored below `featured_min` | threshold or rubric | adjust `sections.<name>.featured_min` or the rubric |

## 5. Incidents

### LLM credentials

Symptom: 401/403 from the endpoint, failing at `rank` or `write`. Check `LLM_BASE_URL` and `LLM_API_KEY` in `~/.config/agent-newsletter/env` and `.env` (`.env` wins), fix the value, then `./run.sh --failed`. Never paste a key into an issue or log.

### A source is down

A failing source is logged (`<family>/<id>: collector failed: ...`) and skipped; the run still succeeds as long as one source produced items, so **this is silent unless you look**. Check the fetch summary line in `logs/<slug>/run-<date>.log`:

```bash
grep -E 'collector failed|DONE: seen=' logs/$SLUG/run-$(date +%F).log
```

Confirm it is the source and not you (`curl -sI <url>`). If it is temporary, do nothing: the next daily run tries again. To retry today, `./run.sh --refetch --edition <slug>` (a plain re-run skips fetch once it has completed). If the source is gone, remove it from `sources.yaml`. Details: [source-reliability-matrix.md](source-reliability-matrix.md).

### Disk space

```bash
df -h . ; du -sh logs .worktrees/content .git
```

Logs are not in git and are safe to delete once old (`logs/*/run-*.log`, `logs/*/usage-*.jsonl`); keep recent ones for debugging. `state.db` grows slowly; do not delete it (see "Recovery").

### Cost spike

Compare `tokens_in`/`tokens_out` by day (section 3), then look for the multiplier: a larger candidate pool (new source, looser gate), a more expensive `RANKER_MODEL`/`WRITER_MODEL` (these are global to all editions), or retries and `--force` re-runs (visible as extra rows in `run_attempts`). The levers are listed in [performance-and-cost-guide.md](performance-and-cost-guide.md).

### An edition keeps failing

Take it out of the daily run without losing history: set `status: paused` in `editions/<slug>/edition.yaml` and merge. It stays on the site, labelled paused, and `./run.sh --edition <slug>` still runs it by hand. Do not delete the edition folder while its issues exist on `content`; the site build fails on orphaned issue folders.

## 6. Scheduled maintenance

| When | Task |
|---|---|
| Weekly | Read two or three recent issues for each edition (relevance, noise, gaps); glance at `run_attempts` for repeated failures |
| Monthly | Review section 3 trends; remove sources that have produced nothing featured; delete old logs |
| Quarterly | Re-read `keywords`, `tags`, `reader_profile` and section thresholds against what is now relevant; re-check dependency updates |
| After any edition or prompt change | `uv run scripts/check_edition.py <slug>`, and for rubric changes see [ranking-rubric.md](ranking-rubric.md) |

## 7. Backups and recovery

Durable state is in git: code and editorial config on `main`, `state.db` and issues on `content`. Each edition pushes its own `editions/<slug>/` when it finishes, and a failed attempt pushes `state.db` too. There is nothing separate to back up; keep the remote healthy.

To restore a damaged `state.db` from history:

```bash
git -C .worktrees/content log --oneline -- editions/$SLUG/state.db | head
git -C .worktrees/content checkout <good-sha> -- editions/$SLUG/state.db
```

Do not delete `state.db` to "start fresh". It holds the dedup history; without it, previously published items can be featured again. `--refetch` only removes **today's** fetched items and is not a database reset.

Moving to another machine, and why two machines must not run the same day, are covered in [multi-edition-operations.md](multi-edition-operations.md).

## 8. Common questions

**Pause an edition?** `status: paused` in `edition.yaml`.

**Change the run time?** Edit the `Hour`/`Minute` in `launchd/com.kelly.agent-newsletter.plist` (macOS, 07:00 as shipped) or your crontab (Linux; see the README). Re-run `./launchd/install.sh` after editing a plist.

**Allow slower models?** Set `RANKER_TIMEOUT_S` / `WRITER_TIMEOUT_S` in `.env`.

**Use a different model?** `RANKER_MODEL` / `WRITER_MODEL` in `.env`. They apply to every edition.

**Re-run today after fixing config?** `./run.sh --force --edition <slug>` (spends tokens).

## 9. Asking for help

Before filing an issue, collect: the summary block from `logs/run-<date>.log`, the failing stage's lines from `logs/<slug>/run-<date>.log`, the relevant `run_attempts` rows, and what you already tried. Redact credentials. Check recent changes with `git log --oneline main -10` and `git -C .worktrees/content log --oneline -10`.
