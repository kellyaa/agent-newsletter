# Contributing to AI Agents Daily

Thank you for contributing. This document covers development setup, the test suite, PR workflow, and repo-specific conventions.

---

## Contents

1. [Prerequisites](#prerequisites)
2. [Local setup](#local-setup)
3. [Running the test suite](#running-the-test-suite)
4. [Repository layout and branch model](#repository-layout-and-branch-model)
5. [Adding or removing a feed source](#adding-or-removing-a-feed-source)
6. [PR workflow and naming](#pr-workflow-and-naming)
7. [The `hold` label](#the-hold-label)
8. [Code style](#code-style)
9. [Operational scripts — backfill and replay](#operational-scripts--backfill-and-replay)
10. [docs/superpowers/ — internal agent docs](#docssuperpowers--internal-agent-docs)

---

## Prerequisites

- **macOS** (primary platform — the scheduler uses launchd; Linux is supported for development and CI but the cron wiring is macOS-only)
- [**uv**](https://docs.astral.sh/uv/) — Python package manager (`brew install uv` or the official installer)
- [**Node.js**](https://nodejs.org/) **≥ 18.17.1** (CI uses Node 22; required by Astro 5 — `brew install node` or use `nvm`)
- [**pnpm**](https://pnpm.io/) — Node package manager for the Astro site (`npm install -g pnpm`)
- [**gh**](https://cli.github.com/) — GitHub CLI (`brew install gh`, then `gh auth login`) — required by `scripts/fetch.py` for the GitHub source adapter; also used for PR/issue workflows
- Access to an **OpenAI-compatible chat-completions endpoint** (OpenAI, vLLM, llama.cpp, LM Studio, Together, Fireworks, OpenRouter, Groq, etc.) — needed only if you intend to run the full pipeline; not needed for code or test work

---

## Local setup

```bash
# 1. Clone
git clone https://github.com/kellyaa/agent-newsletter.git
cd agent-newsletter

# 2. Python deps (creates .venv/)
uv sync

# 3. Site deps
pnpm --prefix site install

# 4. LLM credentials (needed only for full pipeline runs)
cp .env.template .env
$EDITOR .env          # fill in LLM_BASE_URL, LLM_API_KEY, RANKER_MODEL, WRITER_MODEL

# 5. (Optional) Set up the content worktree for local Astro dev
#    The content branch holds state.db and issued YYYY-MM-DD.md files.
git worktree add .worktrees/content content
```

**Running the full pipeline manually:**

```bash
./run.sh              # idempotent; skips completed stages
./run.sh --force      # reset today's post-fetch state and re-run
./run.sh --refetch    # also re-fetch (use sparingly; arXiv rate-limits)
```

**Scheduling the pipeline (macOS only):**

```bash
./launchd/install.sh
```

> ⚠️ **Before running `install.sh`**, edit the two plist files in `launchd/` to replace the hard-coded paths (`/Users/kelly/git/incubation/`) with the actual path to your clone and log directory. `install.sh` copies the plists verbatim without substituting paths. See comments inside each plist for the fields that need updating.

**Running the Astro dev server:**

The Astro dev server reads issue files from `.worktrees/content/site/src/content/issues/`. It requires the content worktree to be set up (step 5 above).

```bash
pnpm --prefix site dev
```

**Building the site (with search index):**

```bash
pnpm --prefix site build
```

The build script runs `astro build && pagefind --site dist`. [Pagefind](https://pagefind.app/) generates the static search index in `dist/pagefind/` after the Astro build. The `pagefind` package is a dev dependency installed by `pnpm install` — no separate install needed. The Astro dev server (`astro dev`) does **not** run pagefind, so search is only functional in the built/previewed output.

---

## Running the test suite

```bash
# Run all tests with coverage
uv run --extra test pytest

# Same, with HTML coverage report
uv run --extra test pytest --cov-report=html

# Run a specific test file
uv run --extra test pytest tests/test_fetch_adapters.py -v
```

The coverage gate is configured in `pyproject.toml` under `[tool.pytest.ini_options] addopts`. Do not lower it. If you add new code, add tests for it. The current gate is `--cov-fail-under=95`; as of PRs #43-#61 (402 tests), line coverage is 100% and branch coverage is ~99%.

CI (`.github/workflows/tests.yml`) runs on every PR and push to `main`. A PR with failing tests or coverage below the gate will not be merged.

---

## Repository layout and branch model

The repo uses two long-lived branches:

| Branch | Holds | Who writes |
|--------|-------|-----------|
| `main` | Scripts, tests, prompts, site scaffold, deploy config | Humans (PRs) |
| `content` | `state.db`, `site/src/content/issues/*.md` | Daily pipeline (automated) |

The two branches never merge. `run.sh` writes to `content` via a git worktree at `.worktrees/content`. Human contributors work on `main` exclusively — you should never need to commit to `content` manually.

`CONTENT_ROOT` is an environment variable set by `run.sh` to point at the content worktree. Scripts that need `state.db` or the issues directory read this variable; they fall back to `cwd` if it is not set (useful for tests).

---

## Adding or removing a feed source

Feed sources are declared in `sources.yaml`. Each source has an `id`, `url`, and optional overrides:

- `section: papers | news | blogs` — override the family-default section assignment
- `keyword_gate_bypass: true` — skip the prefilter keyword gate (use only for low-volume, hand-curated practitioner blogs)
- `recency_days: N` — override the default recency window (useful for slow-publishing sources)
- `weight: <float>` — **reserved stub; currently a no-op** (not read by pipeline code). Present on all existing entries. Do not change weight values expecting any behavioral effect.

**To add a source:**
1. Add an entry under the correct family key (`rss:`, `arxiv:`, `hn:`, etc.) in `sources.yaml`.
2. Run `./run.sh --refetch` to pull from the new source and verify it produces items.
3. Check the prefilter log to confirm items pass or are gated as expected.

**To remove a source:**
1. Delete the entry from `sources.yaml`.
2. Items already in `state.db` from that source are not deleted; they will age out naturally.

---

## PR workflow and naming

1. Branch from `main`: `git checkout -b <your-branch>`.
2. Make changes; add tests if adding or changing code.
3. Commit with DCO sign-off: `git commit -s -m "..."` (the `-s` flag adds a `Signed-off-by:` trailer).
4. Open a PR against `main`.

**Branch naming conventions (conventional, not enforced):**
- `fix/<description>` — bug fixes
- `feat/<description>` — new features
- `docs/<description>` — documentation only
- `quality/<description>` — tests, coverage, CI improvements

**Commit message style:** `<scope>: <short description>` (e.g. `rank: fix burst cap trigger`, `docs: update README setup section`).

---

## The `hold` label

A PR labeled `hold` (or `on-hold` / `do-not-merge`) **must not be merged** until a human removes the label. This is used by the Hive agent framework to create hold-gated PRs that require human review before merging. Do not remove the label unless you are the designated reviewer and have reviewed the PR.

---

## Code style

- Python 3.11+; no type annotations required but they are welcome.
- No external formatter enforced (no black/ruff CI gate), but keep code readable.
- Keep scripts self-contained — `scripts/` files should not import from each other except via `db.py` and `llm.py`.
- New scripts that call the LLM should use `scripts/llm.py` — do not add new `openai` direct calls outside `llm.py`.
- Tests live in `tests/`; use `pytest` fixtures via `tests/conftest.py` (see existing tests for patterns).

---

## Operational scripts — backfill and replay

Two scripts handle emergency and verification scenarios. Neither is part of the normal daily pipeline.

### `scripts/backfill.py` — reconstruct a missed day's issue

Use when a daily run was skipped entirely (e.g., the Mac was off) and you want to produce an issue from the historical candidate pool that was already in `state.db`.

```bash
# Reconstruct the issue for a past date from the DB candidate pool
uv run scripts/backfill.py --date 2026-06-10
```

**Prerequisites:** The content worktree must exist at `.worktrees/content` (run `git worktree add .worktrees/content content` if not). The `CONTENT_ROOT` env var must point to it, or the script falls back to `cwd` (not what you want here).

**Important limitations:**
- Backfill uses candidates that were already scored in `state.db` for that date — it does not re-fetch or re-rank.
- A `runs` row is **not** recorded for a backfilled issue; the run history will show a gap.

### `scripts/replay_writer.py` — replay writer against a past issue

Use when you've changed `prompts/write.md` and want to compare the new writer output against a previously published issue.

```bash
# Replay the writer for a past date (reads published items from state.db)
uv run scripts/replay_writer.py --date 2026-06-10
```

The output goes to stdout (not written to the content branch). Compare the output against the existing issue file to evaluate prompt changes.

---

## docs/superpowers/ — internal agent docs

The `docs/superpowers/` directory contains **internal planning and design documents for agentic workers** (plans, specs, implementation guides). These are not user-facing documentation and are not intended for human contributors. Do not modify files in this directory unless you are implementing a superpowers plan.
