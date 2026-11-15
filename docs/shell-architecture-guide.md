# Shell Architecture Guide

## Overview

The agent-newsletter pipeline is orchestrated through a layer of **shell scripts** that coordinate Python stages, manage execution locks, handle logging, and control the content branch. This guide explains the shell architecture, the **contracts** that define how shell libraries work, and patterns for understanding and extending orchestration logic.

## Why Shell?

The pipeline's heart is Python (fetch, prefilter, rank, write, publish), but orchestration is pure shell for these reasons:

1. **Scheduler compatibility** — launchd (macOS) and cron (Linux) invoke shell scripts directly
2. **Git operations** — committing and pushing content-branch updates is easiest in shell
3. **Process management** — running multiple editions in parallel, taking locks, and tracking PIDs
4. **Environment flexibility** — loading `.env` files and selecting Python interpreters before Python starts

All shell scripts are POSIX-compatible bash and can run on macOS and Linux without modification.

---

## The Contract Pattern

Shell libraries in this project follow a **single-source contract** pattern: each library's functionality lives in exactly one file (enforced by `tests/test_*_single_source.py`). This pattern ensures:

- **No hidden dependencies** — sourcing a library has predictable behavior
- **Testability** — tests verify that the contract is not split across files
- **Discoverability** — developers know where to find orchestration logic

Each library declares its contract in a header comment, which specifies:
- **Purpose** — what problem it solves
- **Functions** — public API (what other scripts call)
- **Inputs/Outputs** — environment variables read, shell variables/arrays set
- **Failure modes** — what happens when things go wrong

---

## The Six Shell Contracts

### 1. runlog.sh — Run Logging and Notifications

**Purpose:** Provides unified run-log formatting, status markers, and alert notifications.

**Used by:** `run.sh`, `scripts/run_edition.sh`, `scripts/watchdog.sh`, `watchdog.sh`

**Functions:**
- `ts` — print an ISO timestamp
- `log <message>` — write a log line with timestamp to the run log
- `notify <title> <body>` — send a notification (macOS `osascript`, Linux `notify-send`, or webhook POST)

**Environment variables (read):**
- `EDITION` — current edition slug (included in log file path)
- `WATCHDOG_WEBHOOK_URL` (optional) — POST every alert here (e.g., ntfy topic URL)

**Run-log paths (set by `run_edition.sh`):**
- `logs/<slug>/run-YYYY-MM-DD.log` — full log for one edition's run
- `logs/run-YYYY-MM-DD.log` — orchestrator summary (one line per edition)

**Standard markers:**
- `── <stage> ── start` — stage starting
- `── <stage> ── ok` — stage completed successfully
- `[run] DONE` — run completed (at end of edition log)
- `[run] FAILED at stage=<name>` — run failed at a stage

**Example:**
```bash
source scripts/runlog.sh
log "fetch: fetching $(date) items..."
# ... do work ...
notify "Pipeline failed" "rank.py exited 1 on edition=$EDITION"
```

---

### 2. pyrunner.sh — Python Interpreter Selection

**Purpose:** Selects which Python interpreter to use, with fallback chain for robustness.

**Used by:** `run.sh`, `scripts/watchdog.sh`

**Functions:**
- `pyrunner_select <repo-root> <mode>` — set `PY_RUN` (array) and `PY_RUN_SOURCE` (why it was chosen)

**Modes:**
- `run` — production use (in `run.sh`): tries `NEWSLETTER_PYTHON`, then `uv run python` (with 45-second timeout), then `.venv/bin/python`, then fails
- `check` — hourly watchdog (in `scripts/watchdog.sh`): tries `NEWSLETTER_PYTHON`, then `.venv/bin/python`, then `python3` (no uv timeout to prevent watchdog stalling)

**Environment variables (read):**
- `NEWSLETTER_PYTHON` (optional) — override the selection (e.g., `"/usr/bin/python3"` or `"uv run python"`)

**Shell variables (set):**
- `PY_RUN` (array) — command to run Python (e.g., `("uv" "run" "python")` or `("/home/user/.venv/bin/python")`)
- `PY_RUN_SOURCE` (string) — why it was chosen and where it came from

**Failure modes:**
- Returns 1 if no usable Python found (on `run` mode, or `check` mode with no fallback)
- Returns 2 if mode is unknown
- No return = success

**Why the timeout?** On pod infrastructure, `uv` is a lazy installer that can hang if it cannot reach GitHub (see header comments in `scripts/pyrunner.sh`). The 45-second timeout prevents `run.sh` stalling when `uv` is broken.

**Example:**
```bash
source scripts/pyrunner.sh
if ! pyrunner_select "$(pwd)" run; then
  echo "ERROR: no python interpreter available" >&2
  exit 1
fi
"${PY_RUN[@]}" scripts/fetch.py || exit 1
```

---

### 3. envfiles.sh — Environment Configuration

**Purpose:** Loads environment variables from `.env` files in a standard order.

**Used by:** `run.sh`, `scripts/watchdog.sh`, `watchdog.sh`

**Functions:**
- `envfiles_load <repo-root>` — source `~/.config/agent-newsletter/env` (if it exists), then `.env` (in the repo), with latter overriding former

**Environment variables (read):**
- `HOME` — used to find `~/.config/agent-newsletter/env`

**Variables set by `.env` / `~/.config/agent-newsletter/env`:**
- `LLM_BASE_URL`, `LLM_API_KEY` — LLM endpoint credentials
- `RANKER_MODEL`, `WRITER_MODEL` — model IDs
- `STALE_HOURS` (optional) — watchdog threshold (default 36)
- `WATCHDOG_WEBHOOK_URL` (optional) — webhook URL for alerts
- `NEWSLETTER_PYTHON` (optional) — override Python interpreter selection
- `BUDGET_USD` (optional, unused yet) — cost cap

**Pattern:** Uses `set -a; . ~/.config/…; . .env; set +a` to source files with all variables exported automatically (later files override earlier ones).

**Example:**
```bash
source scripts/envfiles.sh
envfiles_load "$(pwd)"
echo "LLM_BASE_URL=$LLM_BASE_URL"  # now set
```

---

### 4. runlock.sh — Single-Run Lock

**Purpose:** Ensures only one `run.sh` instance runs at a time, preventing concurrent runs that would corrupt `state.db`.

**Used by:** `run.sh`, `scripts/watchdog.sh`

**Functions:**
- `runlock_acquire` — take the lock (waits if another run holds it; returns 1 if lock is unavailable)
- `runlock_held` — check if the lock is currently held
- `runlock_result` — returns 0 if lock is held, or a special result on platforms without `flock`

**Lock files (gitignored):**
- `.watchdog.lock` — held by `run.sh` for the duration of a full run (all editions)
- `.watchdog-self.lock` — held by `scripts/watchdog.sh` (the self-heal watchdog) while it checks and re-runs editions

**Environment variables (read/written):**
- `WATCHDOG_LOCK_FD` — file descriptor holding the lock (internal, do not modify)
- `WATCHDOG_LOCK_PATH` — path to the lock file (internal, do not modify)

**Failure modes:**
- Returns 2 if `flock` is not installed (critical: `run.sh` exits with error and notifies the user)
- Returns 1 if the lock is already held by another process
- Returns 0 (success) if lock was acquired

**Why `flock`?** It's the portable way to ensure atomic lock acquisition across shell scripts. On macOS, install via Homebrew: `brew install util-linux`.

**Example:**
```bash
source scripts/runlock.sh
if ! runlock_acquire .watchdog.lock; then
  echo "another run is in progress" >&2
  exit 1
fi
# ... do work under lock ...
# lock is released automatically when shell exits
```

---

### 5. contentbranch.sh — Content Branch Git Operations

**Purpose:** Encapsulates the contract for reading/writing the `content` branch, which holds `state.db` and issue files.

**Used by:** `run.sh`, `scripts/run_edition.sh`, `scripts/watchdog.sh`, `watchdog.sh`

**Functions:**
- `content_worktree_path` — return the path to the content worktree (`.worktrees/content`)
- `content_failed_subject <stage>` — return the commit subject for a failed run (e.g., `"newsletter: … attempt 3 failed at fetch"`)
- `content_run_subject <date>` — return the commit subject for a successful run (e.g., `"newsletter: 2026-11-15 daily run"`)

**Environment variables (read):**
- `EDITION` — current edition (used for commit messages)

**Regex patterns (defined in the file):**
- `CONTENT_DAILY_RUN_RE` — regex matching successful daily-run commits; used by `watchdog.sh` to measure staleness

**Git operations:**
```bash
# Commit to content branch
git -C "$(content_worktree_path)" add "editions/$EDITION/"
git -C "$(content_worktree_path)" commit -m "$(content_run_subject 2026-11-15)"
git -C "$(content_worktree_path)" push origin content
```

**Worktree setup:** The content branch is an orphan branch (no shared history with `main`) that lives at `.worktrees/content`. First-time setup:
```bash
git worktree add .worktrees/content content
```

**Example:**
```bash
source scripts/contentbranch.sh
CONTENT_DIR="$(content_worktree_path)"
git -C "$CONTENT_DIR" add "editions/$EDITION/"
git -C "$CONTENT_DIR" commit -s -m "$(content_run_subject 2026-11-15)"
git -C "$CONTENT_DIR" push origin content
```

---

### 6. editionlist.sh — Edition Query Contract

**Purpose:** Provides a stable interface to query edition status (live, draft, paused, invalid).

**Used by:** `run.sh`, `scripts/watchdog.sh`

**Functions:**
- `editionlist_load` — call `scripts/edition.py list`, parse the output into shell associative arrays, exit 1 if the call fails
- `editionlist_is_paused <slug>` — return 0 (true) if edition is paused, 1 (false) otherwise
- `editionlist_is_invalid <slug>` — return 0 (true) if edition is invalid (corrupted config), 1 (false) otherwise

**Shell variables set (after `editionlist_load`):**
- `EDITIONLIST_SLUGS` (array) — all edition slugs
- `EDITIONLIST_STATUSES[<slug>]` (associative array) — status for each edition (`live`, `draft`, `paused`, `invalid`)
- `EDITIONLIST_NAMES[<slug>]` (associative array) — human-readable name for each edition

**Python interface:** `scripts/edition.py list` prints one `slug<TAB>status<TAB>name` line per edition, suitable for parsing.

**Example:**
```bash
source scripts/editionlist.sh
editionlist_load
for slug in "${EDITIONLIST_SLUGS[@]}"; do
  status="${EDITIONLIST_STATUSES[$slug]}"
  [ "$status" = "live" ] && ./run.sh --edition "$slug"
done
```

---

## Orchestration Flow

The pipeline orchestration follows this hierarchy:

```
launchd / cron
    │
    └─ ./run.sh (main orchestrator)
         │ sources: envfiles.sh, runlock.sh, pyrunner.sh, runlog.sh, contentbranch.sh, editionlist.sh
         │
         ├─ (for each edition) → ./scripts/run_edition.sh
         │                           │ sources: runlog.sh, pyrunner.sh, contentbranch.sh
         │                           │
         │                           ├─ uv run scripts/fetch.py
         │                           ├─ uv run scripts/prefilter.py
         │                           ├─ uv run scripts/rank.py
         │                           ├─ uv run scripts/write.py
         │                           └─ uv run scripts/publish.py
         │
         └─ (after all editions) git push content branch

watchdog.sh (hourly, monitors staleness)
    │ sources: runlog.sh, contentbranch.sh
    │
    └─ check if `content` has a `newsletter: <date> daily run` commit within STALE_HOURS
       └─ if not, notify

scripts/watchdog.sh (self-heal, runs frequently)
    │ sources: envfiles.sh, runlock.sh, pyrunner.sh, editionlist.sh, runlog.sh
    │
    ├─ check if each edition's log ends in DONE
    ├─ for editions that didn't: run.sh --edition <slug>
    └─ report status (HEALED, ALREADY_RAN, FAILED, etc.)
```

**Key points:**
1. `run.sh` acquires `.watchdog.lock` for the entire run (prevents concurrent runs)
2. Editions run sequentially (`order` field determines order; `agents` first)
3. Each edition runs in its own child process under `run.sh`, so failures don't block others
4. Python stages are invoked via `pyrunner.sh` to handle interpreter selection gracefully
5. Logging goes to `logs/<slug>/run-<date>.log` with standard markers
6. Content branch is pushed after each edition commits (so failures are visible)

---

## Test Enforcement

Each shell contract has a corresponding test that enforces its single-source constraint:

| Library | Test | Purpose |
|---------|------|---------|
| `runlog.sh` | `tests/test_run_log_single_source.py` | Verify `runlog.sh` is the only file that defines `ts`, `log`, `notify` |
| `pyrunner.sh` | `tests/test_python_runner_single_source.py` | Verify `pyrunner.sh` is the only file that defines `pyrunner_select` |
| `envfiles.sh` | `tests/test_env_files_single_source.py` | Verify `envfiles.sh` is the only file that defines `envfiles_load` |
| `runlock.sh` | `tests/test_run_lock_single_source.py` | Verify `runlock.sh` is the only file that defines `runlock_*` functions |
| `contentbranch.sh` | `tests/test_content_branch_single_source.py` | Verify `contentbranch.sh` is the only file that defines content-branch functions |
| `editionlist.sh` | `tests/test_edition_list_single_source.py` | Verify `editionlist.sh` is the only file that defines `editionlist_*` functions |

These tests prevent accidental code duplication or "function creep" into multiple files.

---

## Patterns for Working with the Orchestration Layer

### Reading the Shell Scripts

1. **Start with `run.sh`** — it's the entry point, ~200 lines, reads like orchestration pseudocode
2. **Follow sourced files in order** — each library solves one problem
3. **Check the test file** — `tests/test_*_single_source.py` shows what functions are public

### Adding a New Orchestration Concept

If you need to add a new shell library (e.g., a "configuration validation" contract):

1. **Create `scripts/your_contract.sh`** with clear header documentation
2. **Add a test** `tests/test_your_contract_single_source.py` to enforce single-source
3. **Source it in `run.sh`** where it's used
4. **Document it here** in this guide

### Debugging Orchestration

When `run.sh` fails or behaves unexpectedly:

1. **Check the log file** — `logs/run-$(date +%Y-%m-%d).log` shows orchestrator summary
2. **Check the edition log** — `logs/<slug>/run-$(date +%Y-%m-%d).log` shows full output with markers
3. **Check the lock** — `ls -la .watchdog.lock` to see if a lock is stuck
4. **Trace a function** — source the library manually and call its functions in bash to debug
5. **Check environment** — `envfiles_load` issues often stem from unset `.env` variables

Example debug session:
```bash
cd /path/to/agent-newsletter
source scripts/envfiles.sh
envfiles_load .
echo "LLM_BASE_URL=$LLM_BASE_URL"
source scripts/pyrunner.sh
pyrunner_select . run && echo "PY_RUN=${PY_RUN[*]}"
```

### Modifying run.sh

`run.sh` is the orchestrator. Before modifying it:

1. **Understand the flags** — `--edition`, `--force`, `--refetch`, `--failed`
2. **Preserve idempotency** — if `run.sh` is killed and restarted, it should resume gracefully
3. **Preserve the lock** — releases only when `run.sh` exits (do not manually release it)
4. **Test the flow** — manually run `./run.sh --edition agents` and verify logs

---

## Related Documentation

- [CONTRIBUTING.md](../CONTRIBUTING.md) — PR workflow, branch model
- [operations-runbook.md](operations-runbook.md) — common operational tasks
- [run-strategies-and-recovery.md](run-strategies-and-recovery.md) — when to use `--force`, `--refetch`, `--failed`
- [ai-ops-runbook.md](ai-ops-runbook.md) — incident response and monitoring

---

*For the full implementation details, read the source files:*
- `run.sh`
- `scripts/run_edition.sh`
- `scripts/*.sh` (each library)
- `tests/test_*_single_source.py` (test enforcement)
