# Python-Shell Integration Guide

How Python scripts receive configuration from shell orchestration, and which environment variables control behavior across the two layers.

---

## The Integration Layer

The pipeline has two layers:
- **Shell layer** (`run.sh`, `scripts/run_edition.sh`, watchdog scripts) — orchestrates editions, manages state, controls concurrency
- **Python layer** (`scripts/fetch.py`, `prefilter.py`, `rank.py`, `write.py`, `publish.py`) — implements pipeline stages

They communicate through:
1. **Environment variables** — set by shell, read by Python
2. **Exit codes** — set by Python, checked by shell
3. **Files on disk** — state.db, candidates.json, logs, issues

---

## Environment Variables Passed to Python

| Var | Set by | Read by | Purpose | Example |
|-----|--------|---------|---------|---------|
| `EDITION` | `scripts/run_edition.sh` | All stage scripts | Selects which edition to run | `agents` |
| `CONTENT_ROOT` | `run.sh` | All stage scripts, `backfill.py` | Path to content worktree | `.worktrees/content` |
| `NEWSLETTER_PYTHON` | `run.sh` via `pyrunner.sh` | `scripts/run_edition.sh` | Python interpreter to use | `/usr/local/bin/python3` or `uv run python` |
| `RUN_STARTED_AT` | `scripts/runlog.sh` | `run_attempt.py`, log entries | ISO-8601 start timestamp | `2026-11-14T07:00:42Z` |
| `LLM_BASE_URL` | `.env` file | `llm.py` | LLM endpoint | `https://api.openai.com/v1` |
| `LLM_API_KEY` | `.env` file | `llm.py` | Bearer token | `sk-...` |
| `RANKER_MODEL` | `.env` file | `rank.py` via `llm.py` | Model for ranking | `gpt-4o-mini` |
| `WRITER_MODEL` | `.env` file | `write.py` via `llm.py` | Model for writing | `gpt-4o` |
| `RANKER_TIMEOUT_S` | `.env` file | `llm.py` | Timeout for ranker calls (optional) | `1800` |
| `WRITER_TIMEOUT_S` | `.env` file | `llm.py` | Timeout for writer call (optional) | `1200` |
| `RANKER_MAX_TOKENS` | `.env` file | `llm.py` | Max completion tokens (optional) | `32000` |
| `WRITER_MAX_TOKENS` | `.env` file | `llm.py` | Max completion tokens (optional) | `16000` |
| `LLM_RESPONSE_FORMAT` | `.env` file | `llm.py` | Structured output mode (optional) | `json_schema` |
| `LLM_RETRY_NO_CACHE` | `.env` file | `llm.py` | Retry cache opt-out (optional) | `1` |
| `STALE_HOURS` | `.env` file | `watchdog.sh` | Hours threshold for stale alerts | `36` |
| `WATCHDOG_WEBHOOK_URL` | `.env` file | `scripts/runlog.sh` | Webhook for headless alerts (optional) | `https://ntfy.sh/mytopic` |

---

## How Environment Variables Flow Through a Run

### 1. Run Startup (`run.sh`)

```bash
#!/usr/bin/env bash
REPO_ROOT="$(pwd)"

# Load environment files (from home, then repo)
. scripts/envfiles.sh
envfiles_load "$REPO_ROOT"

# Now all .env variables are available:
# - LLM_BASE_URL, LLM_API_KEY, RANKER_MODEL, WRITER_MODEL
# - STALE_HOURS, WATCHDOG_WEBHOOK_URL (shell-only)

# Select Python interpreter, export it
. scripts/pyrunner.sh
NEWSLETTER_PYTHON=$(pyrunner_select "$REPO_ROOT" run)
export NEWSLETTER_PYTHON  # ← now available to child processes

# Set content root, export it
. scripts/contentbranch.sh
CONTENT_ROOT="$(content_worktree_path)"
export CONTENT_ROOT  # ← now available to scripts/run_edition.sh
```

### 2. Per-Edition Run (`scripts/run_edition.sh agents`)

```bash
#!/usr/bin/env bash
EDITION="$1"   # ← called with edition slug from run.sh
export EDITION  # ← now available to Python stage scripts

# Run stages sequentially via NEWSLETTER_PYTHON (already exported by run.sh)
$NEWSLETTER_PYTHON scripts/fetch.py       # reads EDITION, CONTENT_ROOT
$NEWSLETTER_PYTHON scripts/prefilter.py   # reads EDITION, CONTENT_ROOT
$NEWSLETTER_PYTHON scripts/rank.py        # reads EDITION, CONTENT_ROOT, LLM_* vars
$NEWSLETTER_PYTHON scripts/write.py       # reads EDITION, CONTENT_ROOT, LLM_* vars
$NEWSLETTER_PYTHON scripts/publish.py     # reads EDITION, CONTENT_ROOT
```

### 3. Inside a Stage Script (`scripts/rank.py`)

```python
import os
import sys
from edition import current

# Read shell-provided env vars
edition = current()          # reads EDITION from os.environ (fails if unset)
content_root = os.getenv('CONTENT_ROOT', '.')
base_url = os.getenv('LLM_BASE_URL')
api_key = os.getenv('LLM_API_KEY')

# Read model selection
from llm import ranker_config
config = ranker_config()  # reads RANKER_MODEL, RANKER_TIMEOUT_S, RANKER_MAX_TOKENS from env

# Use them
print(f"Ranking {edition.slug} edition")
print(f"State DB: {content_root}/editions/{edition.slug}/state.db")
```

---

## Key Variables and Their Lifecycle

### `EDITION`

- **Set by:** `scripts/run_edition.sh <slug>` (exports it)
- **Read by:** All stage scripts via `edition.current()`
- **Lifecycle:** Must be set before any stage script runs; never changes during a run
- **No default:** Fails if unset (intentional; you must explicitly choose an edition)

**Example:**
```bash
# run.sh calls:
./scripts/run_edition.sh agents

# scripts/run_edition.sh does:
EDITION=agents
export EDITION

# Then calls stage scripts, which read it:
# scripts/fetch.py → edition.current() → looks up $EDITION → loads editions/agents/edition.yaml
```

### `CONTENT_ROOT`

- **Set by:** `run.sh` via `content_worktree_path` (exported)
- **Read by:** All stage scripts, `backfill.py`, `replay_writer.py`
- **Default:** Current working directory if unset (useful for tests)
- **Lifecycle:** Set once at run start; never changes

**Example:**
```bash
# run.sh does:
CONTENT_ROOT="$(content_worktree_path)"  # returns .worktrees/content
export CONTENT_ROOT

# Then scripts read it:
# scripts/write.py → write.issues_dir() → returns {CONTENT_ROOT}/editions/{edition}/issues
```

### `RUN_STARTED_AT`

- **Set by:** `scripts/runlog.sh` as an ISO-8601 timestamp
- **Read by:** `run_attempt.py` to record attempt start time
- **Lifecycle:** Set at the start of each run; shared across all stages

**Example:**
```bash
# scripts/runlog.sh exports:
export RUN_STARTED_AT="2026-11-14T07:00:42Z"

# run_attempt.py reads it:
started_at = os.getenv('RUN_STARTED_AT')
# Records in run_attempts table for this run
```

### LLM Configuration (`LLM_BASE_URL`, `LLM_API_KEY`, `RANKER_MODEL`, etc.)

- **Set by:** User in `.env` file (sourced by `run.sh` via `envfiles.sh`)
- **Read by:** `scripts/llm.py` (not directly by stage scripts)
- **Lifecycle:** Same for every call in a run; can vary between runs
- **Default:** Some have defaults in `llm.py` (see `ranker_config()`, `writer_config()`)

**Example:**
```bash
# User's .env:
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
RANKER_MODEL=gpt-4o-mini
WRITER_MODEL=gpt-4o

# run.sh sources .env, then:
./scripts/run_edition.sh agents

# scripts/rank.py calls:
from llm import ranker_config
config = ranker_config()  # reads RANKER_MODEL, timeout, max_tokens from env
```

---

## Exit Codes: How Python Signals Back to Shell

Every stage script must exit with a meaningful code:

| Code | Meaning | Action by Shell |
|------|---------|-----------------|
| 0 | Success | Continue to next stage |
| 1 | Failure (data/config problem) | Stop, mark stage failed, alert |
| 2 | Usage error (missing arg, bad option) | Stop, mark as error |
| Other | Unexpected failure | Stop, mark as error |

**Example:**
```bash
# scripts/run_edition.sh
for stage in fetch prefilter rank write publish; do
  $NEWSLETTER_PYTHON scripts/$stage.py
  rc=$?
  if [ $rc -ne 0 ]; then
    log_stage_failure "$stage" "$rc"
    exit "$rc"  # Shell will see this exit code and stop
  fi
done
```

---

## Special Cases: Ad-Hoc Execution Outside `run.sh`

When running a stage script manually (e.g., for debugging), you must set the env vars yourself:

```bash
# Missing: Python interpreter selection, EDITION, CONTENT_ROOT
uv run scripts/fetch.py        # ← Will fail; EDITION is unset

# Correct: set them first
export EDITION=agents
export CONTENT_ROOT=.worktrees/content

# Now it works:
uv run scripts/fetch.py        # ← OK

# Or for all stages:
set -a; . .env; set +a  # Load env vars
export EDITION=agents
export CONTENT_ROOT=.worktrees/content
for stage in fetch prefilter rank write publish; do
  echo "Running $stage..."
  uv run scripts/$stage.py || break
done
```

---

## Testing the Interface

Tests must not rely on environment variable state set outside the test process. Instead, they mock or set exactly what they need:

```python
# BAD: Depends on external env vars
def test_fetch():
    from fetch import load_sources
    # If EDITION is not set, this fails mysteriously

# GOOD: Set explicitly for the test
import os
os.environ['EDITION'] = 'agents'
os.environ['CONTENT_ROOT'] = '/tmp/test-content'

def test_fetch():
    from fetch import load_sources
    # Now EDITION and CONTENT_ROOT are available
```

See [Testing Patterns Guide](./testing-patterns-guide.md) for more.

---

## Troubleshooting Integration Issues

### "Edition not found" or "EDITION is unset"

**Symptom:**
```
edition.EditionError: EDITION is not set; pick one with EDITION=<slug> uv run scripts/rank.py
```

**Cause:** `EDITION` env var is not set. Stage script couldn't find it.

**Fix:**
```bash
export EDITION=agents
uv run scripts/rank.py
```

### "state.db not found" or `no such table`

**Symptom:**
```
sqlite3.OperationalError: unable to open database file
```

**Cause:** `CONTENT_ROOT` points to wrong place, or `.worktrees/content` doesn't exist.

**Fix:**
```bash
# Verify the worktree exists
ls -la .worktrees/content/

# If missing, create it:
git worktree add .worktrees/content content

# Verify CONTENT_ROOT is exported
echo $CONTENT_ROOT
export CONTENT_ROOT=.worktrees/content
```

### "LLM calls failing with HTTP 401" or "Invalid API key"

**Symptom:**
```
httpx.HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/chat/completions'
```

**Cause:** `LLM_API_KEY` is unset or incorrect in `.env`.

**Fix:**
```bash
# Check .env exists and has credentials
cat .env | grep LLM_API_KEY

# If missing:
cp .env.template .env
$EDITOR .env  # fill in your API key

# Or set in shell:
export LLM_API_KEY='sk-...'
```

### "`flock` not found" or "cannot acquire lock"

**Symptom:**
```
[run] STATUS: FAILED 2026-11-14 no-flock
```

**Cause:** `flock` command is missing (common on macOS).

**Fix:**
```bash
# Check if flock is installed
command -v flock || echo "not found"

# Install via Homebrew (macOS)
brew install util-linux

# Or install from source:
# https://github.com/discoteq/flock
```

---

## References

- [Shell Architecture Guide](./shell-architecture-guide.md) — Understanding `scripts/runlog.sh`, `contentbranch.sh`, `envfiles.sh`, etc.
- [Environment Configuration Reference](./environment-configuration-reference.md) — Full list of all env vars
- [LLM Integration Guide](./llm-integration-guide.md) — How LLM calls work and model selection
- [Code Patterns Guide](./code-patterns-guide.md) — General patterns for stage scripts
- [AGENTS.md § Rules](../AGENTS.md) — Authoritative rules (e.g., "fetch never imports from rank")

---

*Last updated: 2026-11-14*
