# AI Agents Daily

A daily, opinionated digest on building and running AI agents — for senior software engineers and architects. The pipeline runs unattended on a Mac (or Linux), fetches across ~20 sources (RSS, arXiv, HN, Reddit), ranks every item via direct OpenAI-compatible chat-completions calls, writes editorial prose for the top 12-ish, and publishes a static site to GitHub Pages.

The site is at: **https://kellyaa.github.io/agent-newsletter**

## What's here

```
SPEC.md                 — design doc; canonical reference
editions/
  agents/               — the "AI Agents Daily" edition: all editorial config, no code
    edition.yaml        — name, reader profile, keyword gate, tag vocabulary, per-section thresholds/caps
    sources.yaml        — feed list with per-source overrides
    prompts/            — fragments filled into the base prompts (`<stage>.<slot>.md`)
  _template/            — heavily commented starting point copied by `scripts/new_edition.py` (not a real edition)
prompts/
  rank.md               — ranker base template (rubric mechanics + `{{slots}}` for edition wording)
  write.md              — writer base template (output schema, theme rules + `{{slots}}`)
  draft_edition.md      — meta-prompt `scripts/new_edition.py` sends to the LLM to draft a new edition
scripts/
  fetch.py              — collectors (no LLM); INSERT OR IGNORE into state.db
  prefilter.py          — recency + keyword + dedup gates; writes candidates.json as debug artifact
  candidates.py         — shared candidate pool query (DB → grouped dict); used by rank.py and backfill.py
  rank.py               — three LLM calls (OpenAI-compatible), one per section; writes ranked.json as debug artifact
  write.py              — one LLM call (OpenAI-compatible); emits editions/<slug>/issues/YYYY-MM-DD.md (on the content branch)
  llm.py                — thin wrapper around OpenAI-compatible chat-completions
  publish.py            — promotes items to 'published'; records runs row
  run_attempt.py        — records each edition run in run_attempts; `latest` reports today's outcome (called by run_edition.sh and run.sh)
  reset_day.py          — resets one edition's state for `--force` / `--refetch` (called by run_edition.sh)
  migrate_layout.py     — one-time, idempotent content-branch move to `editions/<slug>/` (called by run.sh)
  edition.py            — load + strictly validate an edition; `current()` reads `$EDITION` (no default)
  prompts.py            — render prompts/{rank,write}.md with an edition's fragments
  new_edition.py        — scaffold `editions/<slug>/` from the template, optionally LLM-drafted and machine-validated; writes `REVIEW.md`
  check_edition.py      — lint an edition and dry-run fetch + prefilter against a throwaway DB (no LLM): per-source counts
  db.py                 — schema, URL canonicalization
  models.py             — TypedDict definitions for pipeline stage boundaries; Status and Section StrEnum constants
  backfill.py           — reconstruct a missed day's issue from the candidate pool snapshot
  replay_writer.py      — replay writer against a past date's published items (prompt verification)
run.sh                  — daily orchestrator: runs every edition in turn, one child process each; idempotent
scripts/run_edition.sh  — one edition's pipeline (fetch → … → publish → commit + push `editions/<slug>/`); started by `run.sh`
scripts/watchdog.sh     — self-heal wrapper for the daily run: re-runs `run.sh --edition …` for editions whose log lacks `DONE`; see "Self-heal watchdog" below (distinct from the root `watchdog.sh`)
watchdog.sh             — fires macOS notification if the `content` branch has no successful `newsletter: <date> daily run` commit in >36h (see §Troubleshooting)
launchd/                — plists + install.sh for the two daily/hourly jobs
site/                   — Astro 5 static site (the published surface)
.github/workflows/      — Pages deploy workflow
editions/<slug>/state.db — SQLite pipeline state, one per edition (on the 'content' branch; see "Repository layout")
```

## Editions

The pipeline is not tied to one subject. Each newsletter is an **edition**: a folder of editorial config under `editions/<slug>/` (reader profile, keyword gate, tags, thresholds, sources, prompt fragments), with all code shared. `agents` ("AI Agents Daily") is the first. Select one with `EDITION=<slug>`; there is no default.

To start another, run `uv run scripts/new_edition.py <slug> --name "…" --describe "…"` (add `--no-llm` for a bare template). It drafts the config with the LLM, checks every feed, query, subreddit and regex it proposed, writes a `REVIEW.md` checklist, and runs `uv run scripts/check_edition.py <slug>`, which prints per-source fetched → recency → keyword → section counts without calling the LLM. New editions start as `status: draft`; see CONTRIBUTING.md, "Adding an edition", for the draft → tune → `live` procedure.

## Daily pipeline

```
┌─────────────┐  ┌────────────┐  ┌───────────┐  ┌───────────┐  ┌────────────┐
│  fetch.py   │→ │ prefilter  │→ │  rank.py  │→ │  write.py │→ │ publish.py │
│ (RSS/arXiv  │  │ (gates,    │  │ (3× LLM,  │  │ (1× LLM,  │  │ (promote,  │
│  /HN/gh…)   │  │  dedup)    │  │  per sec.)│  │  prose)   │  │  runs row) │
└─────────────┘  └────────────┘  └───────────┘  └───────────┘  └────────────┘
                                                       │
                                                       ▼
                                          editions/<slug>/issues/YYYY-MM-DD.md
                                                       │
                                                       ▼ git push → content branch →
                                                       ▼ deploy.yml (main + content) →
                                                       ▼ Astro build → Pages deploy
```

Every stage is idempotent. Re-running `run.sh` after a transient failure picks up where it left off; nothing pays the LLM cost twice. `run.sh --force` resets today's post-fetch state for a clean re-run.

**Several editions, one job.** `run.sh` runs every `draft` and `live` edition one after another (by `order`, `agents` first), each in its own child process (`scripts/run_edition.sh`). An edition commits and pushes its own `editions/<slug>/` on the `content` branch as soon as it finishes, and a failure never blocks the others: you get one notification per failed edition (including drafts), naming the edition and stage, and `run.sh` exits non-zero at the end.

| Command | Does |
|---|---|
| `./run.sh` | all `draft` + `live` editions; skips an edition whose latest attempt today is `ok` |
| `./run.sh --failed` | only editions whose latest attempt today `failed` or died mid-run; "nothing to re-run" and exit 0 if none |
| `./run.sh --edition X` (repeatable) | only these editions; paused editions are allowed |
| `--force` / `--refetch` | per selected edition; `--refetch` requires `--edition` so it can't hit arXiv for every edition at once |

Logs: `logs/run-DATE.log` is the orchestrator summary (one line per edition); `logs/<slug>/run-DATE.log` is that edition's full output and ends in `DONE` on success. Per-edition LLM usage and debug artifacts (`candidates.json`, `ranked.json`, rendered prompts) are under `logs/<slug>/`.

**Self-update:** At the start of every run, `run.sh` does a `git pull --ff-only` on the current branch (and `git pull --ff-only` on the content worktree). This ensures pipeline code, prompts, and site scaffold are current before content is generated. On the launchd daily run this is a catchup on `main`; on a feature branch it updates that branch. If the pull fails (dirty working tree or non-fast-forward), `run.sh` aborts and fires a macOS notification.

## Setup (one-time)

**Prereqs — macOS and Linux both supported:**

- [**uv**](https://docs.astral.sh/uv/) — Python package manager
- [**Node.js ≥ 18.17.1**](https://nodejs.org/) + [**pnpm**](https://pnpm.io/) — for the Astro site build
- An **OpenAI-compatible chat-completions endpoint** (OpenAI, vLLM, llama.cpp, Together, Groq, etc.)
- **`flock`** (util-linux) — `run.sh` takes a single-run lock with it. Present on Linux; **not part of stock macOS** (install one, e.g. via Homebrew — verify the formula for your setup). If `flock` is missing, `run.sh` logs `another run holds .watchdog.lock; exiting without doing anything` and **exits 0**, so a scheduled job looks healthy while never running the pipeline (see Troubleshooting §3)
- The [**gh**](https://cli.github.com/) CLI is only needed if you re-enable the `github_releases:` source in `editions/agents/sources.yaml` (disabled by default since 2026-05-14)

The pipeline scripts (`run.sh`, `scripts/`) are POSIX-compatible and run on Linux without modification. The only macOS-only feature is `watchdog.sh`, which uses `osascript` for desktop notifications (gracefully no-ops on non-macOS).

```bash
# Python deps
uv sync

# Site deps
pnpm --prefix site install

# LLM credentials — copy the template and fill in your values
cp .env.template .env
$EDITOR .env
```

**Scheduling (macOS):** first apply the path substitutions in the warning below, *then* run `./launchd/install.sh` to install the daily-run and hourly-watchdog launchd jobs. Linux: see the next paragraph.

**Linux scheduler:** The `launchd/` plists are macOS-only. On Linux, use cron or a systemd user service:

```bash
# crontab -e — run pipeline at 07:00 daily, watchdog hourly
0 7 * * *  /path/to/agent-newsletter/run.sh >> /path/to/agent-newsletter/logs/launchd.out 2>> /path/to/agent-newsletter/logs/launchd.err
0 * * * *  /path/to/agent-newsletter/watchdog.sh >> /path/to/agent-newsletter/logs/watchdog.out 2>&1
```

> ⚠️ **Before running `./launchd/install.sh`:** the plist files under `launchd/` contain
> hard-coded author paths (`/Users/kelly/git/incubation/…`). Substitute your actual repo
> root and local-bin path with these two `sed` commands (run from the repo root):
>
> ```bash
> REPO_ROOT="$(pwd)"
> LOCAL_BIN="$HOME/.local/bin"   # adjust if uv/pnpm live elsewhere (e.g. /opt/homebrew/bin)
>
> sed -i '' \
>   -e "s|/Users/kelly/git/incubation|${REPO_ROOT}|g" \
>   -e "s|/Users/kelly/.local/bin|${LOCAL_BIN}|g" \
>   launchd/com.kelly.agent-newsletter.plist \
>   launchd/com.kelly.agent-newsletter-watchdog.plist
> ```
>
> Verify the result with `grep -r '/Users/kelly' launchd/` — it should print nothing.
> Installing without making these substitutions will silently install plists that point at
> the wrong location and the scheduled job will never run.

Once the substitutions are done and `grep` is clean, install the jobs:

```bash
./launchd/install.sh
```

### LLM configuration (`.env`)

The ranker and writer scripts read their endpoint, key, and model ids from environment variables. `run.sh` sources `.env` (repo-local, gitignored) at startup; you can also put values in `~/.config/agent-newsletter/env` for machine-wide defaults that `.env` overrides.

| Var | Required | Purpose |
|---|---|---|
| `LLM_BASE_URL` | yes | Endpoint base URL, e.g. `https://api.openai.com/v1` |
| `LLM_API_KEY` | yes | Bearer token (some local servers accept any non-empty value) |
| `OPENAI_API_KEY` | no | Fallback bearer token if `LLM_API_KEY` is unset; recognized by `scripts/llm.py` for compatibility with standard OpenAI tooling |
| `RANKER_MODEL` | yes | Model id for the per-section ranker (small/cheap is fine) |
| `WRITER_MODEL` | yes | Model id for the editorial writer (quality matters more) |
| `RANKER_TIMEOUT_S` | no | Per-call timeout, default 1800 |
| `WRITER_TIMEOUT_S` | no | Per-call timeout, default 1200 |
| `RANKER_MAX_TOKENS` | no | Max completion tokens for ranker calls, default 32000 |
| `WRITER_MAX_TOKENS` | no | Max completion tokens for writer call, default 16000 |
| `LLM_EXTRA_HEADERS` | no | JSON object of extra headers to send on every request |
| `LLM_RESPONSE_FORMAT` | no | Structured-output mode: `json_schema` (default; strict schema, honored by OpenAI, vLLM, llama.cpp, LM Studio), `json_object`, or `none` (rely on the prompt). Any other value raises. Use `json_object` for providers that reject `json_schema` — e.g. LiteLLM-fronted Bedrock Claude returns HTTP 400. |
| `LLM_RETRY_NO_CACHE` | no | Default `1`: when `llm.py` retries after a parse/truncation failure it sends `extra_body={"cache": {"no-cache": true}}` (LiteLLM per-request cache opt-out) so the retry isn't served the identical bad reply. Set `0` for endpoints that reject unknown body params. |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | no | Reddit "script" app credentials (create at https://www.reddit.com/prefs/apps). Reddit blocks unauthenticated `top.json` (HTTP 403), so the fetcher uses the app-only OAuth grant and `oauth.reddit.com` when both are set. When either is unset the `reddit` sources are skipped with one `reddit: skipped` info line instead of a collector failure per source. |
| `REDDIT_USERNAME` | no | Reddit username for the required User-Agent (`python:agent-newsletter:v0.1 (by /u/<name>)`); defaults to `agent-newsletter`. |
| `BUDGET_USD` | no | **Not yet enforced.** Scaffolded for a future per-run cost cap; The `runs.cost_usd` column exists but is **not populated** (always NULL): per-run tokens are recorded in `runs.tokens_in` / `tokens_out` and per-model tokens in `runs.notes`, and cost is priced downstream from those (see SPEC.md §Cost Budget). |
| `STALE_HOURS` | no | Hours without a successful daily-run commit (`newsletter: <date> daily run`) on the `content` branch before the watchdog fires a notification; default `36` (set in `watchdog.sh`). The watchdog throttles re-firing to once per 4 hours even if the stale condition persists (prevents notification spam during a prolonged outage). |
| `CONTENT_ROOT` | set by `run.sh` | Path to the content worktree (`.worktrees/content`). Set automatically by `run.sh`; must be exported manually when running scripts ad-hoc outside `run.sh`. Falls back to `cwd` if unset (useful for tests). |

Use `LLM_EXTRA_HEADERS` for endpoints that require additional auth/routing headers beyond the bearer token. Examples:

```bash
LLM_EXTRA_HEADERS='{"RITS_API_KEY": "xyz"}'
LLM_EXTRA_HEADERS='{"X-Tenant-Id": "abc"}'
```

When invoked outside `run.sh` (e.g. ad-hoc `uv run scripts/rank.py`), export the variables yourself or run via `set -a; . .env; set +a; uv run …`.

To trigger a run on demand (without waiting for 07:00):

```bash
./run.sh                # idempotent
./run.sh --force        # re-run today from scratch (skips re-fetching; arXiv safe)
./run.sh --refetch      # also re-fetch from all sources (use sparingly; arXiv rate-limits)
```

To uninstall the schedulers:

```bash
./launchd/install.sh --uninstall
```

## Development

Python tests: `uv run --extra test pytest`

Site tests (Node.js 22.6+): `cd site && pnpm test`

## Forking to run your own instance

You can fork this repo and run your own personal newsletter with minimal changes:

1. **Fork** the repo on GitHub.

2. **Enable GitHub Pages** in your fork's Settings → Pages → Source: "GitHub Actions". The existing `.github/workflows/deploy.yml` workflow deploys the site automatically on push to `main` or `content`.

3. **Create the `content` orphan branch** (the daily pipeline writes `state.db` and issue files here):
   ```bash
   git checkout --orphan content
   git rm -rf .
   git commit --allow-empty -m "init content branch"
   git push origin content
   git checkout main
   ```

4. **Set up `.env`** per the [LLM configuration](#llm-configuration-env) instructions above.

5. **Install the scheduler** via `./launchd/install.sh` (edit the plists first — see the ⚠️ warning in the Setup section).

6. **Optionally customize `editions/agents/sources.yaml`** to add, remove, or override feed sources for your interests.

No additional secrets or environment variables are required in the GitHub Actions workflow — the deploy is a pure Astro build from the committed Markdown.

## Cost & runtime

A typical run takes ~10 minutes wall time, most of it the three ranker LLM calls plus the one writer call. Cost depends entirely on which models you point `RANKER_MODEL` / `WRITER_MODEL` at. Per-run token usage is logged in the `runs` table (`tokens_in` / `tokens_out`, and per-model tokens in `notes`); the `cost_usd` column is not populated, so price the tokens against your provider's rates.

## state.db

`state.db` is a SQLite file tracked on the **`content` branch** (not `main` — see "Repository layout" below). It's small (single-digit MB, growing slowly) and holding it in git is deliberate: the daily scheduled run is self-healing, and re-cloning `content` on a new machine reproduces dedup state, run history, and topic coverage tracking without a bootstrap step. Because commits go up alongside daily runs, the DB at `content`'s HEAD roughly matches what's live at https://kellyaa.github.io/agent-newsletter/. (Soft: individual runs can fail or be retried, so this is a match "as of the last successful publish," not a strict invariant.)

The DB contains only a cache of public RSS/HN/arXiv/GitHub items plus the ranker's LLM-generated scores and rationales — **no secrets, no PII, no credentials**. LLM endpoints and keys live in `.env` (gitignored).

Four tables:

- **`items`** — every fetched item, with status (`new` / `candidate` / `featured` / `appendix` / `published` / `dropped`), assigned section, ranker score, tags, and dedup metadata (canonical URL, first/last-seen dates, appearance count). (Note: `ranked` is never written as a status; `rank.py` transitions directly from `candidate` to `featured`/`appendix`/`dropped`.)
- **`runs`** — one row per published issue: item counts by section, wall-clock duration of the publishing attempt, and `tokens_in` / `tokens_out` summed over every attempt that day (#13; filled when the LLM endpoint reports usage). Models and per-model tokens are in `notes`. `papers_expired_unjudged` and `papers_prerank_cut` record how many papers that day's age-out expired without ever being scored and how many unscored papers `PAPER_PRERANK_CAP` cut from the ranker call (#297; NULL on rows from before it). Prefilter stages them in `run_stage_stats` and publish copies them onto the row. `cost_usd` stays empty: cost is priced downstream from the per-model tokens.
- **`run_attempts`** — one row per `run.sh` invocation, failed ones included (`scripts/run_attempt.py`): start and finish time, `ok` / `failed` (or `started` if the run died mid-way), the failed stage and exit code, the code commit it ran, and that attempt's tokens and models. After a failure, `run.sh` commits and pushes `state.db` alone to `content`, so failures are visible off the pod. Nothing in the pipeline reads this table.
- **`topics_covered`** — reserved for cross-day topic dedup (see #4); tracks which topic slugs the ranker has already featured.

**Schema auto-migration.** `db.py` runs `_migrate()` on every `init_db()` call, applying any missing `ALTER TABLE` column additions automatically. Upgrading your checkout and re-running the pipeline is safe — the DB schema self-heals. No manual migration needed.

See [SPEC.md § Data Model](./SPEC.md#data-model-sqlite) for the column-level schema.

## Design

Read [SPEC.md](./SPEC.md) for the full design rationale: the section-aware rubric, dedup strategy, source-section override mechanism, and editorial voice guide.

## Repository layout

The repo uses two long-lived branches, split by *authorship*:

- **`main`** — human-authored code: scripts, tests, prompts, site scaffold, deploy config, `pyproject.toml`. This is where PRs land and where code changes are reviewed. Generated deploy artifacts are gitignored on this branch.
- **`content`** — an *orphan branch* (no shared history with `main`) that holds machine-authored deploy artifacts: `state.db` and `site/src/content/issues/*.md`. `run.sh` writes here via a worktree at `.worktrees/content` and pushes on every daily run.

The two branches never merge. This keeps `main`'s history readable (no daily automated commits obscuring code changes) and cleanly separates "what humans decided to change" from "what the pipeline produced." The pattern is the same one GitHub Pages uses for its `gh-pages` branch.

**Deploy combines the two.** `.github/workflows/deploy.yml` triggers on push to either branch. It checks out `main` as the primary tree (site scaffold, config, `package.json`) and `content` into a subpath (`_content/`), copies each `_content/editions/<slug>/issues/` to `site/src/content/issues/<slug>/`, then runs Astro's build. The site reads the edition registry (`editions/*/edition.yaml`, `draft` editions hidden unless `SHOW_DRAFTS=1`) at build time and fails the build on issue content that no edition owns. Routes: `/` (edition picker), `/<edition>/`, `/<edition>/issues/<date>/`, `/<edition>/months/<yyyymm>/`, `/<edition>/tags/`, `/<edition>/rss.xml`, `/<edition>/search/`; `/rss.xml` is kept as a copy of `/agents/rss.xml`. The Astro build itself is the deploy gate — a malformed issue file fails the build and the last-good deploy stays live.

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
- **LLM call fails with HTTP 400 on structured output** (typical on LiteLLM/Bedrock-style proxies). Try `LLM_RESPONSE_FORMAT=json_object` in `.env`. If the 400 appears only on a retry, try `LLM_RETRY_NO_CACHE=0`.
- **rank stuck on a section.** `rank.py` makes one OpenAI-compatible chat-completions call per section (papers / news / blogs) via `scripts/llm.py`. If a section's "ranker returned N entries" log line never appears, the HTTP call hasn't returned. Check the PID's start time against now and the `RANKER_TIMEOUT_S` setting.
- **`run.sh` exits instantly with "another run holds .watchdog.lock" but nothing is running.** Either a run really is in flight (`ps aux | grep run.sh`) or `flock` is not installed (`command -v flock`); in the latter case the lock guard fires on the missing command and `run.sh` exits 0 without doing any work. Install `flock` (see Prerequisites).
- **watchdog says `HEAD is not a newsletter commit … skipping check`.** `watchdog.sh` measures staleness from the newest `newsletter: <date> daily run` commit on the `content` branch (the `.worktrees/content` worktree, or the local/`origin/content` ref if there is no worktree). Failure-recovery commits (`… attempt N failed at <stage>`, `persist state.db from interrupted …`) do not count. It only falls back to main's HEAD, and prints this message when HEAD is not a `newsletter:` commit, if there is no content-branch history at all (e.g. a checkout that has never run the pipeline). To double-check the pipeline independently, query the `runs` table:
  ```bash
  sqlite3 .worktrees/content/state.db "SELECT date, items_featured, tokens_in, tokens_out FROM runs ORDER BY date DESC LIMIT 1;"
  ```
- **watchdog notification throttle.** Once the stale-pipeline notification fires, `watchdog.sh` will not re-fire for 4 hours (throttle state in `logs/watchdog-last-fire`). If you want to force an immediate re-check after diagnosing the problem, run: `rm -f logs/watchdog-last-fire && ./watchdog.sh`

### Self-heal watchdog (`scripts/watchdog.sh`)

Not to be confused with the root `watchdog.sh` (the macOS stale-commit notifier). `scripts/watchdog.sh` checks, for every `draft`/`live` edition, whether today's `logs/<slug>/run-YYYY-MM-DD.log` ends in `DONE` and, if not, re-runs just those editions with `run.sh --edition …`. It probes the same lock `run.sh` holds (`.watchdog.lock`, gitignored; the watchdog's own `.watchdog-self.lock` is likewise gitignored) so it never starts a second pipeline on top of a live one. Its last stdout line is a status line:

| Status line | Meaning | Exit |
|---|---|---|
| `EDITION: <slug> ALREADY_RAN\|IN_PROGRESS\|HEALED <date>` / `EDITION: <slug> FAILED <date> <stage>` | one line per checked edition, before the final `STATUS:` line | — |
| `STATUS: ALREADY_RAN <date>` | every edition's log exists and ended in `DONE` | 0 |
| `STATUS: IN_PROGRESS <date>` | a run is mid-flight; left alone | 0 |
| `STATUS: HEALED <date>` | an edition's log was missing/incomplete; it re-ran it successfully | 0 |
| `STATUS: FAILED <date> <slug>:<stage> …` | worst-of: names each edition that failed and its stage | 1 |

```bash
./scripts/watchdog.sh          # check, and run the pipeline if needed
./scripts/watchdog.sh --check  # check only, never run the pipeline
```

### 4. Unsticking it

```bash
# Kill a hung ranker/writer process; run.sh will surface the error and exit
kill <pid-of-rank.py-or-write.py>

# Or kill the whole pipeline
pkill -f "$(pwd)/run.sh"   # run from the repo root; or kill the PID from `ps aux | grep run.sh`

# Then re-run idempotently — completed stages are skipped
./run.sh

# Or wipe today's post-fetch state and start clean (keeps the fetched candidates)
./run.sh --force
```

### 5. Cross-checks

```bash
# Did publish.py record a run today?
sqlite3 .worktrees/content/state.db "SELECT date, items_featured, items_papers, items_news, items_blogs, tokens_in, tokens_out FROM runs ORDER BY date DESC LIMIT 5;"

# Is there a newsletter file for today?
ls .worktrees/content/site/src/content/issues/$(date +%Y-%m-%d).md 2>/dev/null && echo present || echo missing
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
