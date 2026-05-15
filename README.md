# AI Agents Daily

A daily, opinionated digest on building and running AI agents — for senior software engineers and architects. The pipeline runs unattended on a Mac, fetches across ~20 sources (RSS, arXiv, HN, Reddit, GitHub releases), ranks every item with Claude, writes editorial prose for the top 12-ish, and publishes a static site to GitHub Pages.

The site is at: **https://kellyaa.github.io/agent-newsletter** *(once deploy lands)*.

## What's here

```
SPEC.md                 — design doc; canonical reference
sources.yaml            — feed list with per-source overrides
prompts/
  rank.md               — ranker rubric (per-section thresholds)
  write.md              — writer voice/style + JSON output schema
scripts/
  fetch.py              — collectors (no LLM); INSERT OR IGNORE into state.db
  prefilter.py          — recency + keyword + dedup gates
  rank.py               — three claude -p calls, one per section
  write.py              — emits site/src/content/issues/YYYY-MM-DD.md
  publish.py            — promotes items to 'published'; records runs row
  db.py                 — schema, URL canonicalization
run.sh                  — daily orchestrator; idempotent
watchdog.sh             — fires macOS notification if no commit in >36h
launchd/                — plists + install.sh for the two daily/hourly jobs
site/                   — Astro 5 static site (the published surface)
.github/workflows/      — Pages deploy workflow
state.db                — SQLite (committed; small enough; serves as the audit log)
```

## Daily pipeline

```
┌─────────────┐  ┌────────────┐  ┌───────────┐  ┌───────────┐  ┌────────────┐
│  fetch.py   │→ │ prefilter  │→ │  rank.py  │→ │  write.py │→ │ publish.py │
│ (RSS/arXiv  │  │ (gates,    │  │ (3× LLM,  │  │ (1× LLM,  │  │ (promote,  │
│  /HN/gh…)   │  │  dedup)    │  │  per sec.)│  │  prose)   │  │  runs row) │
└─────────────┘  └────────────┘  └───────────┘  └───────────┘  └────────────┘
                                                       │
                                                       ▼
                                          site/src/content/issues/YYYY-MM-DD.md
                                                       │
                                                       ▼ git push → GitHub Actions →
                                                       ▼ Astro build → Pages deploy
```

Every stage is idempotent. Re-running `run.sh` after a transient failure picks up where it left off; nothing pays the LLM cost twice. `run.sh --force` resets today's post-fetch state for a clean re-run.

## Setup (one-time)

Prereqs: macOS, [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), [gh](https://cli.github.com/), Claude Code installed and authenticated.

```bash
# Python deps
uv sync

# Site deps
pnpm --prefix site install

# Schedule the daily run + hourly watchdog
./launchd/install.sh
```

To trigger a run on demand (without waiting for 07:00):

```bash
./run.sh                # idempotent
./run.sh --force        # re-run today from scratch
```

To uninstall the schedulers:

```bash
./launchd/install.sh --uninstall
```

## Cost & runtime

A typical run takes ~10 minutes wall time (most of it the three ranker LLM calls and the writer call), at roughly **$1-1.50 in Claude API costs**. Per-run cost is logged in the `runs` table.

## Design

Read [SPEC.md](./SPEC.md) for the full design rationale: the section-aware rubric, dedup strategy, source-section override mechanism, and editorial voice guide.

## Troubleshooting a stalled run

If the morning run never produced a newsletter commit, walk the pipeline stage by stage. The launchd job writes everything to `logs/`.

### 1. What got run, and where did it stop?

```bash
# Today's pipeline log — stage banners are "── <stage> ── start/ok"
cat logs/run-$(date +%Y-%m-%d).log

# Same content from launchd's perspective (stdout of run.sh)
tail -100 logs/launchd.out
tail logs/launchd.err
```

The last `── <stage> ── start` without a matching `── ok` is where the pipeline is stuck.

### 2. Is anything still running?

```bash
ps aux | grep -E "run.sh|fetch.py|prefilter|rank.py|write.py|claude -p" | grep -v grep
```

A live `run.sh` plus a `claude -p` subprocess means a ranker or writer LLM call is in flight. Note the start time — sonnet ranking should finish in seconds to a couple minutes; anything past ~10 minutes is anomalous.

### 3. Common stall modes

- **arxiv 429s in fetch.** Look for `arxiv/<name>: collector failed: ... 429`. The collector retries with ~17 min backoff, which can stretch fetch from seconds to ~30+ min. The other collectors continue; fetch exits ok with `errors=N` in the DONE line.
- **rank stuck on a section.** `rank.py` invokes `claude -p` once per section (papers / news / blogs). The log line `invoking claude (<section>, model=sonnet)` with no following `cost ~$X, N turns` line means that subprocess hasn't returned. Check the PID's start time against now.
- **watchdog noise.** `logs/watchdog.out` says `HEAD is not a newsletter commit … skipping check` whenever HEAD is a non-newsletter commit (spec edits, pipeline changes). That's expected; it's not a failure signal.

### 4. Unsticking it

```bash
# Kill a hung claude subprocess; run.sh will surface the error and exit
kill <pid-of-claude-p>

# Or kill the whole pipeline
pkill -f run.sh

# Then re-run idempotently — completed stages are skipped
./run.sh

# Or wipe today's post-fetch state and start clean (keeps the fetched candidates)
./run.sh --force
```

### 5. Cross-checks

```bash
# Did publish.py record a run today?
sqlite3 state.db "SELECT date, status, cost_usd, started_at, finished_at FROM runs ORDER BY started_at DESC LIMIT 5;"

# Is there a newsletter file for today?
ls site/src/content/issues/$(date +%Y-%m-%d).md 2>/dev/null && echo present || echo missing
```
