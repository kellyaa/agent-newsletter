# Shell Architecture Guide

Understanding the shell orchestration layer that coordinates the daily pipeline. The shell scripts are the *only* place certain policies live (log format, environment file loading, git worktree management, run locking), so changes to these contracts must be coordinated between all readers.

---

## Overview: The Five Helper Contracts

The shell layer defines **five contracts** — each is a sourced library with single responsibility. Every contract has a writer (who creates/modifies state) and readers (who consume it). Changing a contract requires modifying both the writer and all readers together.

| Contract | Writer | Readers | Purpose |
|----------|--------|---------|---------|
| [**runlog.sh**](#runlogsh) | `scripts/run_edition.sh` | `run.sh`, `scripts/watchdog.sh`, `watchdog.sh` | Log file format, `DONE`/`FAILED` markers, status checks |
| [**contentbranch.sh**](#contentbranchsh) | `scripts/run_edition.sh` | `run.sh`, `scripts/watchdog.sh`, `watchdog.sh`, `backfill.py` | Content worktree path, commit subjects, staleness detection |
| [**envfiles.sh**](#envfilessh) | User/`.env` files | `run.sh`, `watchdog.sh`, `scripts/watchdog.sh` | Environment file loading order (`~/.config/agent-newsletter/env`, then `.env`) |
| [**pyrunner.sh**](#pyrunnersh) | `run.sh` | `scripts/run_edition.sh`, `scripts/watchdog.sh` | Python interpreter selection (uv → venv → python3) |
| [**runlock.sh**](#runlocksh) | `run.sh`, `scripts/watchdog.sh` | `run.sh`, `scripts/watchdog.sh` | Single-run lock file (`flock`), lock checking |

---

## runlog.sh

**Purpose:** Define the log file format and the markers that signal pipeline completion or failure.

**Location:** `scripts/runlog.sh` (single source; tests/test_run_log_single_source.py enforces this)

### Writer: `scripts/run_edition.sh`

```bash
# Write markers to the per-edition log
runlog_mark_done    # outputs `<ts> [run] DONE` to logs/<slug>/run-<date>.log
runlog_mark_failed  # outputs `<ts> [run] FAILED at stage=<stage> rc=<rc>`
```

### Readers: Status checking

**`run.sh`:**
- Calls `runlog_is_complete <slug> <date>` to check if an edition finished
- Skips re-running editions whose latest attempt is complete
- Gathers all failed editions and exits non-zero if any failed

**`scripts/watchdog.sh` (self-heal):**
- Reads `runlog_is_complete` and `runlog_failed_stage` to find incomplete runs
- Re-runs any edition whose log lacks a `DONE` marker

**`watchdog.sh` (root stale-commit detector):**
- Sources `runlog.sh` to use `notify` (the alert helper)
- Sends macOS/Linux/webhook notifications via `notify`

### The Markers

```bash
# Success marker (written by scripts/run_edition.sh at the end of a successful run)
<ISO-8601 timestamp> [run] DONE

# Failure marker (written when a stage fails)
<ISO-8601 timestamp> [run] FAILED at stage=<stage> rc=<exit-code>

# Example log file (logs/agents/run-2026-11-14.log)
2026-11-14T07:00:42Z [run] fetch: start
2026-11-14T07:01:03Z [run] prefilter: start
2026-11-14T07:01:18Z [run] rank: start
2026-11-14T07:03:45Z [run] write: start
2026-11-14T07:03:52Z [run] publish: start
2026-11-14T07:03:58Z [run] DONE
```

### Stage Names

Stages must match `RUNLOG_STAGE_RE` (a bash regex) defined in `runlog.sh`. Current stages: `fetch`, `prefilter`, `rank`, `write`, `publish`. New stages must be added to the regex before use.

---

## contentbranch.sh

**Purpose:** Define the content branch worktree path and commit subject patterns that identify successful daily runs vs. failures.

**Location:** `scripts/contentbranch.sh` (single source; tests/test_content_branch_single_source.py enforces this)

### Writer: `scripts/run_edition.sh`

```bash
# Get the worktree path (set up by run.sh before run_edition.sh is called)
content_worktree_path  # returns .worktrees/content

# Build commit subjects
content_run_subject "<slug>"           # returns "newsletter(<slug>): <date> daily run"
content_failed_subject "<slug>" "<stage>" "<rc>"  # returns "... attempt N failed at <stage> rc=<rc>"

# Example commits on content branch
commit 1: newsletter(agents): 2026-11-14 daily run (success)
commit 2: persist state.db from interrupted agents run (failure recovery)
commit 3: newsletter(agents): 2026-11-13 daily run (retry after fix)
```

### Readers: Staleness Detection

**`run.sh`:**
- Calls `content_worktree_path` to create/update the git worktree at `.worktrees/content`
- Uses `content_run_subject` to compose the commit message

**`watchdog.sh` (root):**
- Matches the newest commit on `content` against `CONTENT_DAILY_RUN_RE`
- Measures staleness in hours from the last successful daily-run commit
- Alerts if no successful run commit has landed in `STALE_HOURS` (default 36)

**`scripts/watchdog.sh`:**
- Passes `CONTENT_ROOT` to `missed_days.py` and `dead_sources.py`
- Reads issue files to reconstruct what was published

**`backfill.py`:**
- Matches `content_run_subject` format to find the target date's pre-run state in git history
- Rebuilds a missed day's issue from the historical candidate pool

### Commit Subject Regex

```bash
CONTENT_DAILY_RUN_RE="^newsletter(\([a-z0-9-]+\)): .* daily run$"
```

This regex:
- Matches `newsletter(agents): 2026-11-14 daily run` ✓
- Does NOT match `persist state.db from interrupted ...` (failure recovery)
- Does NOT match `... attempt N failed at ...` (retry flag)

---

## envfiles.sh

**Purpose:** Define the order in which environment files are sourced and where shell-only variables (not in `.env.template`) can be set.

**Location:** `scripts/envfiles.sh` (single source; tests/test_env_files_single_source.py enforces this)

### Contract

```bash
envfiles_load "$REPO_ROOT"  # sources both files in order; last wins
```

Files sourced (in order, first match wins):
1. `~/.config/agent-newsletter/env` (user machine defaults)
2. `.env` (repo-local overrides; gitignored)

### Shell-Only Environment Variables

Variables in these files but NOT in `.env.template`:

| Variable | Set by | Read by | Purpose |
|----------|--------|---------|---------|
| `STALE_HOURS` | `.env` or `~/.config/agent-newsletter/env` | `watchdog.sh` (root) | Hours without a successful daily run before alerting; default 36 |
| `WATCHDOG_WEBHOOK_URL` | `.env` or `~/.config/agent-newsletter/env` | `scripts/runlog.sh` (notify function) | POST every alert (e.g., to ntfy.sh topic) on headless hosts; optional |
| `NEWSLETTER_PYTHON` | Exported by `scripts/pyrunner.sh` | `scripts/run_edition.sh` | Path to the Python interpreter selected at run start |

### Usage Pattern

```bash
# How run.sh, watchdog.sh, and scripts/watchdog.sh use it
. scripts/envfiles.sh
envfiles_load "$REPO_ROOT"    # Load before first access to env vars

# Now STALE_HOURS, WATCHDOG_WEBHOOK_URL are available if set
echo "$STALE_HOURS"           # prints 36 (default) or custom value
```

---

## pyrunner.sh

**Purpose:** Select which Python interpreter to use, and export it so child processes (scripts/run_edition.sh) run Python scripts the same way.

**Location:** `scripts/pyrunner.sh` (single source; tests/test_python_runner_single_source.py enforces this)

### Contract

```bash
# Call in run.sh (run mode: uv is required)
NEWSLETTER_PYTHON=$(pyrunner_select "$REPO_ROOT" run)
export NEWSLETTER_PYTHON

# Call in scripts/watchdog.sh (check mode: uv is optional)
NEWSLETTER_PYTHON=$(pyrunner_select "$REPO_ROOT" check)
```

### Selection Logic

**Run mode** (uv required):
1. If `uv` is installed and `uv.lock` exists, use `uv run python`
2. Else if `.venv/bin/python` exists, use that
3. Else exit 1 and fail the run

**Check mode** (uv optional):
1. If `.venv/bin/python` exists, use that
2. Else use `python3` (assume it's on PATH)

### Why This Matters

Without `NEWSLETTER_PYTHON` exported, `scripts/run_edition.sh` would have to decide independently how to run Python. By centralizing the choice in `run.sh`, we ensure:
- Consistency across all child processes
- One place to test and debug Python selection
- Clear dependency on uv/venv availability

---

## runlock.sh

**Purpose:** Implement a single-run lock using `flock` to prevent concurrent pipeline runs.

**Location:** `scripts/runlock.sh` (single source; tests/test_run_lock_single_source.py enforces this)

### Contract

```bash
# Acquire the run lock (used by run.sh at startup)
runlock_acquire "$REPO_ROOT"
# Returns 0 (lock acquired), 1 (lock held), or 2 (flock not found)

# Check if the lock is held (used by scripts/watchdog.sh)
runlock_held "$REPO_ROOT"
# Returns 0 (lock held) or 1 (not held) or 2 (flock not found)
```

### Lock Files

```
.watchdog.lock       — Run lock (held by run.sh during a pipeline run)
.watchdog-self.lock  — Self-heal lock (held by scripts/watchdog.sh during a self-heal run)
```

Both are gitignored and ephemeral.

### Exit Codes on `flock` Not Found

If `flock` is not installed (returns exit code 127), both functions return 2. The caller MUST treat this as a failure:

- **`run.sh`:** Exits 1 and fires an alert `STATUS: FAILED <date> no-flock`
- **`scripts/watchdog.sh`:** Prints `STATUS: FAILED <date> no-flock` and exits 1

A return code of 2 must never be treated as "lock is not held" — that would allow concurrent runs.

---

## How They Work Together: A Concrete Example

### Scenario: Daily run on Nov 14

1. **`run.sh` starts** (07:00)
   - Calls `envfiles_load` to get `STALE_HOURS`, `NEWSLETTER_PYTHON`, etc.
   - Calls `pyrunner_select run` to pick Python interpreter
   - Calls `runlock_acquire` — acquires `.watchdog.lock`
   - Calls `content_worktree_path` — gets `.worktrees/content`
   - Spawns `scripts/run_edition.sh agents`

2. **`scripts/run_edition.sh agents` runs** (07:00–07:04)
   - Sources `runlog.sh` and calls `runlog_mark_done` on success
   - Sources `contentbranch.sh` and calls `content_run_subject agents` for the commit
   - Writes `2026-11-14T07:04:00Z [run] DONE` to `logs/agents/run-2026-11-14.log`
   - Commits to `content` branch with subject `newsletter(agents): 2026-11-14 daily run`
   - Pushes to GitHub

3. **`run.sh` finishes** (07:04)
   - Calls `runlog_is_complete agents 2026-11-14` → returns 0 (complete)
   - Releases `.watchdog.lock`
   - Exits 0

4. **`watchdog.sh` (root) runs hourly** (08:00)
   - Sources `contentbranch.sh` to get `CONTENT_DAILY_RUN_RE`
   - Reads `content` branch HEAD commit subject
   - Matches against `CONTENT_DAILY_RUN_RE` → matches ✓
   - Measures time since commit → 1 hour
   - No alert (stale threshold is 36 hours)

### Scenario: Run fails at rank stage

1. **`scripts/run_edition.sh agents` runs, rank fails**
   - Calls `runlog_mark_failed "rank" "1"` (exit code 1)
   - Writes `2026-11-14T07:03:45Z [run] FAILED at stage=rank rc=1` to log
   - Commits `editions/agents/` with a persistence message

2. **`run.sh` checks status**
   - Calls `runlog_is_complete agents 2026-11-14` → returns 1 (incomplete)
   - Calls `runlog_failed_stage agents 2026-11-14` → returns `rank`
   - Prints alert to the console

3. **`scripts/watchdog.sh` runs later**
   - Reads `logs/agents/run-2026-11-14.log`
   - Calls `runlog_is_complete agents 2026-11-14` → returns 1
   - Calls `runlog_failed_stage agents 2026-11-14` → returns `rank`
   - Re-runs `run.sh --edition agents` to retry

---

## Key Design Principles

1. **Single Responsibility:** Each contract owns exactly one piece of shared state (logs, worktree, env files, Python choice, locks).

2. **Writer + Readers:** The writer (usually `scripts/run_edition.sh` or `run.sh`) defines the format. Readers must parse it the same way. Tests enforce that readers use the shared functions, not inline parsing.

3. **Sourced, Never Imported:** These are shell function libraries, not subshells. They export variables and functions into the caller's environment.

4. **No Hard-Coding:** Code never spells out `.watchdog.lock` or `logs/<slug>/run-<date>.log` directly — it calls `runlock_acquire` or `runlog_is_complete`.

5. **Testable Contracts:** `tests/test_*_single_source.py` ensures that:
   - The contract lives in exactly one file
   - All readers call the shared functions, not reimplemented logic
   - Changes are coordinated (a modification requires updating writer + all readers)

---

## When to Add a New Contract

Add a new contract (sourced library) when:
- Multiple shell scripts need to read/write the same piece of structured state
- The format or logic is complex enough that duplication is error-prone
- A single test (`tests/test_*_single_source.py`) can enforce single-sourcing

Do NOT add a contract for:
- One-off settings (use environment variables)
- Simple variables (define them in the caller)
- Things that change with every use (not a stable contract)

---

## References

- **AGENTS.md § Rules** — Detailed contract specifications for agents
- **Enforcement Tests:**
  - `tests/test_run_log_single_source.py`
  - `tests/test_content_branch_single_source.py`
  - `tests/test_env_files_single_source.py`
  - `tests/test_python_runner_single_source.py`
  - `tests/test_run_lock_single_source.py`
- **Related Guides:**
  - [Operations Runbook](./operations-runbook.md)
  - [Run Strategies and Recovery](./run-strategies-and-recovery.md)
  - [Debugging Guide](./debugging-guide.md)

---

*Last updated: 2026-11-14*
