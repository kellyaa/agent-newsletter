# AI Agents Daily

A daily, opinionated digest on building and running AI agents — for senior software engineers and architects. The pipeline runs unattended on a Mac, fetches across ~20 sources (RSS, arXiv, HN, Reddit), ranks every item via direct OpenAI-compatible chat-completions calls, writes editorial prose for the top 12-ish, and publishes a static site to GitHub Pages.

The site is at: **https://kellyaa.github.io/agent-newsletter**

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
  rank.py               — three LLM calls (OpenAI-compatible), one per section
  write.py              — one LLM call (OpenAI-compatible); emits site/src/content/issues/YYYY-MM-DD.md
  llm.py                — thin wrapper around OpenAI-compatible chat-completions
  publish.py            — promotes items to 'published'; records runs row
  db.py                 — schema, URL canonicalization
  models.py             — TypedDict definitions for pipeline stage boundaries
  backfill.py           — reconstruct a missed day's issue from the candidate pool snapshot
  replay_writer.py      — replay writer against a past date's published items (prompt verification)
run.sh                  — daily orchestrator; idempotent
watchdog.sh             — fires macOS notification if no commit in >36h
launchd/                — plists + install.sh for the two daily/hourly jobs
site/                   — Astro 5 static site (the published surface)
.github/workflows/      — Pages deploy workflow
state.db                — SQLite pipeline state (on the 'content' branch; see "Repository layout")
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
                                                       ▼ git push → content branch →
                                                       ▼ deploy.yml (main + content) →
                                                       ▼ Astro build → Pages deploy
```

Every stage is idempotent. Re-running `run.sh` after a transient failure picks up where it left off; nothing pays the LLM cost twice. `run.sh --force` resets today's post-fetch state for a clean re-run.

## Setup (one-time)

Prereqs: macOS, [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), [gh](https://cli.github.com/), and access to an OpenAI-compatible chat-completions endpoint (OpenAI itself, vLLM, llama.cpp, LM Studio, Together, Fireworks, OpenRouter, Groq, an internal endpoint, etc.).

```bash
# Python deps
uv sync

# Site deps
pnpm --prefix site install

# LLM credentials — copy the template and fill in your values
cp .env.template .env
$EDITOR .env

# Schedule the daily run + hourly watchdog
./launchd/install.sh
```

### LLM configuration (`.env`)

The ranker and writer scripts read their endpoint, key, and model ids from environment variables. `run.sh` sources `.env` (repo-local, gitignored) at startup; you can also put values in `~/.config/agent-newsletter/env` for machine-wide defaults that `.env` overrides.

| Var | Required | Purpose |
|---|---|---|
| `LLM_BASE_URL` | yes | Endpoint base URL, e.g. `https://api.openai.com/v1` |
| `LLM_API_KEY` | yes | Bearer token (some local servers accept any non-empty value) |
| `RANKER_MODEL` | yes | Model id for the per-section ranker (small/cheap is fine) |
| `WRITER_MODEL` | yes | Model id for the editorial writer (quality matters more) |
| `RANKER_TIMEOUT_S` | no | Per-call timeout, default 1800 |
| `WRITER_TIMEOUT_S` | no | Per-call timeout, default 1200 |
| `RANKER_MAX_TOKENS` | no | Max completion tokens for ranker calls, default 32000 |
| `WRITER_MAX_TOKENS` | no | Max completion tokens for writer call, default 16000 |
| `LLM_EXTRA_HEADERS` | no | JSON object of extra headers to send on every request |

Use `LLM_EXTRA_HEADERS` for endpoints that require additional auth/routing headers beyond the bearer token. Examples:

```bash
LLM_EXTRA_HEADERS='{"RITS_API_KEY": "xyz"}'
LLM_EXTRA_HEADERS='{"X-Tenant-Id": "abc"}'
```

When invoked outside `run.sh` (e.g. ad-hoc `uv run scripts/rank.py`), export the variables yourself or run via `set -a; . .env; set +a; uv run …`.

To trigger a run on demand (without waiting for 07:00):

```bash
./run.sh                # idempotent
./run.sh --force        # re-run today from scratch
```

To uninstall the schedulers:

```bash
./launchd/install.sh --uninstall
```

## Development

Tests: `uv run --extra test pytest`

## Cost & runtime

A typical run takes ~10 minutes wall time, most of it the three ranker LLM calls plus the one writer call. Cost depends entirely on which models you point `RANKER_MODEL` / `WRITER_MODEL` at. Per-run cost is logged in the `runs` table.

## state.db

`state.db` is a SQLite file tracked on the **`content` branch** (not `main` — see "Repository layout" below). It's small (single-digit MB, growing slowly) and holding it in git is deliberate: the daily macOS cron run is self-healing, and re-cloning `content` on a new machine reproduces dedup state, run history, and topic coverage tracking without a bootstrap step. Because commits go up alongside daily runs, the DB at `content`'s HEAD roughly matches what's live at https://kellyaa.github.io/agent-newsletter/. (Soft: individual runs can fail or be retried, so this is a match "as of the last successful publish," not a strict invariant.)

The DB contains only a cache of public RSS/HN/arXiv/GitHub items plus the ranker's LLM-generated scores and rationales — **no secrets, no PII, no credentials**. LLM endpoints and keys live in `.env` (gitignored).

Three tables:

- **`items`** — every fetched item, with status (`candidate` / `ranked` / `featured` / `appendix` / `published` / `dropped`), assigned section, ranker score, tags, and dedup metadata (canonical URL, first/last-seen dates, appearance count).
- **`runs`** — one row per daily pipeline run: item counts by section, wall-clock duration; `tokens_in` / `tokens_out` / `cost_usd` columns are scaffolded (see #13) and populated when the LLM endpoint exposes usage.
- **`topics_covered`** — reserved for cross-day topic dedup (see #4); tracks which topic slugs the ranker has already featured.

See [SPEC.md § Data Model](./SPEC.md#data-model-sqlite) for the column-level schema.

## Design

Read [SPEC.md](./SPEC.md) for the full design rationale: the section-aware rubric, dedup strategy, source-section override mechanism, and editorial voice guide.

## Repository layout

The repo uses two long-lived branches, split by *authorship*:

- **`main`** — human-authored code: scripts, tests, prompts, site scaffold, deploy config, `pyproject.toml`. This is where PRs land and where code changes are reviewed. Generated deploy artifacts are gitignored on this branch.
- **`content`** — an *orphan branch* (no shared history with `main`) that holds machine-authored deploy artifacts: `state.db` and `site/src/content/issues/*.md`. `run.sh` writes here via a worktree at `.worktrees/content` and pushes on every daily run.

The two branches never merge. This keeps `main`'s history readable (no daily automated commits obscuring code changes) and cleanly separates "what humans decided to change" from "what the pipeline produced." The pattern is the same one GitHub Pages uses for its `gh-pages` branch.

**Deploy combines the two.** `.github/workflows/deploy.yml` triggers on push to either branch. It checks out `main` as the primary tree (site scaffold, config, `package.json`) and `content` into a subpath (`_content/`), copies `_content/site/src/content/issues/` into place, then runs Astro's build. The Astro build itself is the deploy gate — a malformed issue file fails the build and the last-good deploy stays live.

**Local development.** When iterating on code from `main`, the Astro dev server reads issue files from `.worktrees/content/site/src/content/issues/`. If you don't have the worktree set up locally, create it once: `git worktree add .worktrees/content content`.

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
ps aux | grep -E "run.sh|fetch.py|prefilter|rank.py|write.py" | grep -v grep
```

A live `run.sh` plus a `rank.py` or `write.py` process means a ranker or writer LLM call is in flight against the configured OpenAI-compatible endpoint. Note the start time — small/fast models should finish in seconds to a couple minutes; anything past ~10 minutes is anomalous (consider tuning `RANKER_TIMEOUT_S` / `WRITER_TIMEOUT_S`).

### 3. Common stall modes

- **arxiv 429s in fetch.** Look for `arxiv/<name>: collector failed: ... 429`. The collector retries with ~17 min backoff, which can stretch fetch from seconds to ~30+ min. The other collectors continue; fetch exits ok with `errors=N` in the DONE line.
- **rank stuck on a section.** `rank.py` makes one OpenAI-compatible chat-completions call per section (papers / news / blogs) via `scripts/llm.py`. If a section's "ranker returned N entries" log line never appears, the HTTP call hasn't returned. Check the PID's start time against now and the `RANKER_TIMEOUT_S` setting.
- **watchdog noise.** `logs/watchdog.out` says `HEAD is not a newsletter commit … skipping check` whenever HEAD is a non-newsletter commit (spec edits, pipeline changes). That's expected; it's not a failure signal.

### 4. Unsticking it

```bash
# Kill a hung ranker/writer process; run.sh will surface the error and exit
kill <pid-of-rank.py-or-write.py>

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
sqlite3 .worktrees/content/state.db "SELECT date, status, cost_usd, started_at, finished_at FROM runs ORDER BY started_at DESC LIMIT 5;"

# Is there a newsletter file for today?
ls .worktrees/content/site/src/content/issues/$(date +%Y-%m-%d).md 2>/dev/null && echo present || echo missing
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
