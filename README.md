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
