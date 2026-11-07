# Command Reference

Quick lookup for all pipeline, testing, and operational commands. For detailed usage and examples, see the guide specific to your task in [docs/README.md](README.md).

---

## Pipeline & Scheduling

| Command | Does | See also |
|---------|------|----------|
| `./run.sh` | Run all `draft` + `live` editions today (skips already-ok ones) | [README.md](../README.md#daily-pipeline) |
| `./run.sh --failed` | Re-run only editions that failed today (none if all ok) | [README.md](../README.md#daily-pipeline) |
| `./run.sh --edition X` | Run one edition (repeatable; paused editions allowed) | [README.md](../README.md#daily-pipeline) |
| `./run.sh --force` | Reset today's post-fetch state; skip re-fetch (safe on arXiv) | [README.md](../README.md#daily-pipeline) |
| `./run.sh --refetch --edition X` | Also re-fetch from all sources (use sparingly; arXiv rate-limits) | [README.md](../README.md#daily-pipeline) |
| `./watchdog.sh` | Notify (macOS `osascript`, Linux `notify-send`, optional `WATCHDOG_WEBHOOK_URL` POST) if no successful run in >36h (STALE_HOURS) | [README.md](../README.md#troubleshooting-a-stalled-run) |
| `./scripts/watchdog.sh` | Self-heal wrapper: check and re-run failed editions | [README.md](../README.md#self-heal-watchdog-scriptswatch-dogsh) |
| `./scripts/watchdog.sh --check` | Check only; never run the pipeline | [README.md](../README.md#self-heal-watchdog-scriptswatch-dogsh) |
| `./launchd/install.sh` | Install daily-run and hourly-watchdog launchd jobs (macOS) | [README.md](../README.md#scheduling-macos) |
| `./launchd/install.sh --uninstall` | Uninstall scheduled jobs | [README.md](../README.md#scheduling-macos) |

---

## Edition Management

| Command | Does | See also |
|---------|------|----------|
| `uv run scripts/new_edition.py <slug> --name "…" --describe "…"` | Draft a new edition (uses LLM to scaffold) | [CONTRIBUTING.md § Adding an edition](../CONTRIBUTING.md#adding-an-edition) |
| `uv run scripts/new_edition.py <slug> --name "…" --no-llm` | Create bare template (fill TODOs by hand) | [CONTRIBUTING.md § Adding an edition](../CONTRIBUTING.md#adding-an-edition) |
| `uv run scripts/check_edition.py <slug>` | Lint edition config; dry-run fetch + prefilter (no LLM) | [docs/edition-tuning-guide.md](edition-tuning-guide.md) |

---

## Operational & Recovery

| Command | Does | See also |
|---------|------|----------|
| `uv run scripts/backfill.py --date YYYY-MM-DD` | Reconstruct missed issue from candidate pool (dry-run; doesn't modify state.db) | [CONTRIBUTING.md § scripts/backfill.py](../CONTRIBUTING.md#scriptsbackfillpy--reconstruct-a-missed-days-issue) |
| `uv run scripts/backfill.py --date YYYY-MM-DD --apply-published` | Backfill for real: promote featured items to 'published' in live DB | [CONTRIBUTING.md § scripts/backfill.py](../CONTRIBUTING.md#scriptsbackfillpy--reconstruct-a-missed-days-issue) |
| `uv run scripts/replay_writer.py YYYY-MM-DD` | Re-run writer against published items from a past date (logs to `logs/theme-replay-*.json`) | [CONTRIBUTING.md § scripts/replay_writer.py](../CONTRIBUTING.md#scriptsreplay_writerpy--replay-writer-against-a-past-issue) |
| `uv run scripts/dead_sources.py` | List sources with no items (or only errors) for the last N days (default 7; `dead_after_days` overrides) | [README.md § Self-heal watchdog](../README.md#self-heal-watchdog-scriptswatch-dogsh) |
| `uv run scripts/missed_days.py` | List days with no `run_attempts` row in the last 7 days | [README.md § Self-heal watchdog](../README.md#self-heal-watchdog-scriptswatch-dogsh) |
| `uv run scripts/ai-audit-report.mjs [--since …] [--check]` | Count commits by automation, check for missing DCO sign-off | [README.md § What's here](../README.md#whats-here) |
| `uv run scripts/generate-issues.mjs <backlog.md> [--label …] [--create]` | Auto-generate GitHub issues from markdown backlog (dry-run without `--create`) | [README.md § What's here](../README.md#whats-here) |

---

## Testing

| Command | Does | See also |
|---------|------|----------|
| `uv run --extra test pytest` | Run all tests; stop on first failure | [CONTRIBUTING.md § Running the test suite](../CONTRIBUTING.md#running-the-test-suite) |
| `uv run --extra test pytest -v` | Verbose output (names every test) | [CONTRIBUTING.md § Running the test suite](../CONTRIBUTING.md#running-the-test-suite) |
| `uv run --extra test pytest <path>` | Run tests in one file (e.g., `tests/test_gates.py`) | [CONTRIBUTING.md § Running the test suite](../CONTRIBUTING.md#running-the-test-suite) |
| `uv run --extra test pytest --cov-report=html` | Generate HTML coverage report (opens in browser) | [CONTRIBUTING.md § Running the test suite](../CONTRIBUTING.md#running-the-test-suite) |
| `uv run --extra test pytest --cov-fail-under=95` | Check coverage gate (95% required) | [CONTRIBUTING.md § Running the test suite](../CONTRIBUTING.md#running-the-test-suite) |

---

## Site Development

| Command | Does | See also |
|---------|------|----------|
| `pnpm --prefix site install` | Install Node dependencies (run once, and after `package.json` or lockfile changes) | [CONTRIBUTING.md § Local setup](../CONTRIBUTING.md#local-setup) |
| `pnpm --prefix site dev` | Start dev server (http://localhost:4321) | [CONTRIBUTING.md § Running the Astro dev server](../CONTRIBUTING.md#running-the-astro-dev-server) |
| `pnpm --prefix site build` | Build the site (astro build, then pagefind indexes dist/) | [docs/site-development-guide.md](site-development-guide.md) |
| `pnpm --prefix site preview` | Serve the built site locally (needed to test search) | [docs/site-development-guide.md](site-development-guide.md) |
| `pnpm --prefix site test` | Run site unit tests | [docs/site-development-guide.md](site-development-guide.md) |

---

## Environment & Configuration

| Command | Does | See also |
|---------|------|----------|
| `cp .env.template .env && $EDITOR .env` | Create local `.env` with LLM credentials | [README.md § LLM configuration](../README.md#llm-configuration-env) |
| `set -a; source .env; set +a; uv run scripts/rank.py` | Source `.env` before running a script manually | [README.md § LLM configuration](../README.md#llm-configuration-env) |
| `git worktree add .worktrees/content content` | Set up content worktree for local Astro dev | [CONTRIBUTING.md § Local setup](../CONTRIBUTING.md#local-setup) |

---

## Debugging & Inspection

| Command | Does | See also |
|---------|------|----------|
| `cat logs/run-$(date +%Y-%m-%d).log` | View orchestrator summary (one line per edition) | [README.md § Troubleshooting](../README.md#troubleshooting-a-stalled-run) |
| `tail -100 logs/<slug>/run-$(date +%Y-%m-%d).log` | View full log for one edition | [README.md § Troubleshooting](../README.md#troubleshooting-a-stalled-run) |
| `ps aux \| grep -E "run.sh\|fetch.py\|rank.py\|write.py"` | Check if a run is in progress | [README.md § Troubleshooting](../README.md#troubleshooting-a-stalled-run) |
| `pkill -f "$(pwd)/run.sh"` | Kill the pipeline (from repo root) | [README.md § Troubleshooting](../README.md#troubleshooting-a-stalled-run) |
| `sqlite3 .worktrees/content/state.db "SELECT …"` | Query the pipeline state database | [docs/database-schema-guide.md](database-schema-guide.md) |
| `sqlite3 .worktrees/content/state.db ".schema items"` | Show the `items` table schema | [docs/database-schema-guide.md](database-schema-guide.md) |
| `command -v flock` | Check if `flock` is installed (required for `run.sh` lock) | [README.md § Troubleshooting](../README.md#troubleshooting-a-stalled-run) |

---

## Environment Variables

Set in `.env` or export before running:

| Variable | Purpose | Example |
|----------|---------|---------|
| `EDITION` | Newsletter edition slug to run | `EDITION=agents` |
| `LLM_BASE_URL` | OpenAI-compatible endpoint | `LLM_BASE_URL=https://api.openai.com/v1` |
| `LLM_API_KEY` | Bearer token for LLM endpoint | `LLM_API_KEY=sk-…` |
| `RANKER_MODEL` | Model ID for ranker | `RANKER_MODEL=gpt-4o-mini` |
| `WRITER_MODEL` | Model ID for writer (quality matters more) | `WRITER_MODEL=gpt-4o` |
| `RANKER_TIMEOUT_S` | Ranker timeout (default 1800) | `RANKER_TIMEOUT_S=3600` |
| `WRITER_TIMEOUT_S` | Writer timeout (default 1200) | `WRITER_TIMEOUT_S=1800` |
| `SHOW_DRAFTS=1` | Include draft editions in site build | `SHOW_DRAFTS=1 pnpm --prefix site dev` |
| `FETCH_ALLOW_PRIVATE_HOSTS` | Allow fetching from RFC-1918 addresses (SSRF guard) | `FETCH_ALLOW_PRIVATE_HOSTS=1` |
| `WATCHDOG_WEBHOOK_URL` | Optional URL the root `watchdog.sh` POSTs the stale-pipeline alert to (ntfy-style: title header, message body); additive to desktop notifiers | `WATCHDOG_WEBHOOK_URL=https://ntfy.sh/my-topic` |
| `STALE_HOURS` | Hours without successful run before watchdog alerts (default 36) | `STALE_HOURS=48` |

See [docs/environment-configuration-reference.md](environment-configuration-reference.md) for complete reference.

---

## Git & GitHub

| Command | Does | See also |
|---------|------|----------|
| `git checkout -b <type>/<description>` | Create a branch (types: `fix/`, `feat/`, `docs/`, `quality/`) | [CONTRIBUTING.md § PR workflow](../CONTRIBUTING.md#pr-workflow-and-naming) |
| `git commit -s -m "<scope>: <description>"` | Commit with DCO sign-off (`-s` adds trailer) | [CONTRIBUTING.md § PR workflow](../CONTRIBUTING.md#pr-workflow-and-naming) |
| `gh pr create --title "<type>: <desc>" --body "…"` | Open a pull request | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| `gh issue view <N> --comments` | View issue with comments | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| `gh search prs --repo kellyaa/agent-newsletter --merged` | Find merged PRs | [docs/strategy.md](strategy.md) |

---

## Development Setup (One-time)

```bash
# Python
uv sync

# Site
pnpm --prefix site install

# LLM credentials (if running full pipeline)
cp .env.template .env
$EDITOR .env          # fill in LLM_BASE_URL, LLM_API_KEY, etc.

# Content worktree (for local Astro dev)
git worktree add .worktrees/content content

# Schedule jobs (macOS only)
./launchd/install.sh
```

---

## Typical Workflows

### I want to fix a bug

```bash
git checkout -b fix/description
# edit code
uv run --extra test pytest          # verify tests pass
git commit -s -m "fix: short description"
git push origin fix/description
gh pr create --title "🐛 fix: …" --body "…"
```

### I want to add a feed source

```bash
uv run scripts/check_edition.py agents   # before
# edit editions/agents/sources.yaml
uv run scripts/check_edition.py agents   # after (verify it parses and fetches)
git commit -s -m "sources: add …"
git push origin feat/new-source
gh pr create --title "✨ feat: add … source" --body "…"
```

### I want to troubleshoot a failed run

```bash
cat logs/run-$(date +%Y-%m-%d).log                    # which stage failed?
tail -100 logs/<slug>/run-$(date +%Y-%m-%d).log       # full log for that edition
./run.sh --edition <slug>                             # re-run that edition
./run.sh --edition <slug> --force                     # if it's stuck in prefilter/rank/write
```

### I want to backfill a missed day

```bash
git worktree add .worktrees/content content           # if not set up yet
uv run scripts/backfill.py --date 2026-06-10          # dry-run
uv run scripts/backfill.py --date 2026-06-10 --apply-published  # for real
```

---

## Getting Help

- **Design doc:** [SPEC.md](../SPEC.md)
- **Contributing guide:** [CONTRIBUTING.md](../CONTRIBUTING.md)
- **Task-specific guides:** [docs/README.md](README.md)
- **Issues:** [github.com/kellyaa/agent-newsletter/issues](https://github.com/kellyaa/agent-newsletter/issues)
