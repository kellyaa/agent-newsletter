#!/usr/bin/env bash
#
# Daily orchestrator for the AI-Agents newsletter pipeline.
#
# Stages: fetch → prefilter → rank → write → publish → git push
#
# Each stage is idempotent. A re-run picks up where a prior failure left off:
#   - fetch skips if today's items are already in the DB
#   - prefilter is a no-op if there's nothing new to gate
#   - rank skips items that already have a status past 'candidate'
#   - write skips if today's issue file exists
#   - publish skips if today's runs row exists and items are published
#
# Usage:
#   ./run.sh             — normal daily run; idempotent
#   ./run.sh --force     — reset today's post-fetch state and re-run everything
#                          downstream of fetch (preserves fetched data; arXiv is
#                          rate-limited so we don't redo network pulls)
#   ./run.sh --refetch   — also delete today's fetched items so fetch runs again
#                          from scratch. Implies --force. Use sparingly: arXiv
#                          may 429 if you re-pull too often.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

FORCE=false
REFETCH=false
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    --refetch) REFETCH=true; FORCE=true ;;
    -h|--help)
      # Print the header comment (stops output from going to the run log).
      sed -n '3,21p' "$0"
      exit 0
      ;;
    *) printf 'unknown arg: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"
DATE_TODAY="$(date +%F)"
LOG_FILE="$LOG_DIR/run-$DATE_TODAY.log"

# tee everything through to a per-day log so we can grep failures later.
exec > >(tee -a "$LOG_FILE") 2>&1

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s [run] %s\n' "$(ts)" "$*"; }

# Fire a macOS notification. No-op on non-Darwin or if osascript is missing.
notify() {
  local title="$1"; shift
  local message="$1"; shift
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"${message//\"/\\\"}\" with title \"${title//\"/\\\"}\"" \
      >/dev/null 2>&1 || true
  fi
}

# On any error before DONE, fire a notification and exit nonzero.
fail_handler() {
  local rc=$?
  local stage="${CURRENT_STAGE:-unknown}"
  log "FAILED at stage=$stage rc=$rc — see $LOG_FILE"
  notify "Newsletter pipeline failed" "stage=$stage  rc=$rc — see logs/$(basename "$LOG_FILE")"
  exit "$rc"
}
trap fail_handler ERR

# ─── Environment loading ───────────────────────────────────────────────────
# Load LLM credentials and other config. Sourced in order, so the *last* file
# wins for any given key:
#   1. ~/.config/agent-newsletter/env  — machine-wide defaults (optional)
#   2. .env                            — repo-local, gitignored (overrides)
for ENV_FILE in "$HOME/.config/agent-newsletter/env" "$REPO_ROOT/.env"; do
  if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
  fi
done

# ─── Self-update ───────────────────────────────────────────────────────────
# Pull the current branch so pipeline code, prompts, and site scaffold are
# current before generating content against them. For the launchd daily run
# this is a no-op catchup on main. For a human running ./run.sh from a
# feature branch, it updates that branch. --ff-only lets git's native error
# surface if the working tree is dirty or the pull isn't fast-forward.
log "── self-update ── pulling current branch"
git pull --ff-only

# ─── Content worktree ──────────────────────────────────────────────────────
# The 'content' orphan branch holds machine-authored deploy artifacts
# (state.db, site/src/content/issues/*). Pipeline scripts write into this
# worktree via the CONTENT_ROOT env var. The worktree is created on first
# run and reused thereafter; the pull catches up any commits pushed from
# another machine or a manual edit.
CONTENT_WORKTREE="$REPO_ROOT/.worktrees/content"

if ! git worktree list --porcelain | grep -q "$CONTENT_WORKTREE\$"; then
  log "── content worktree ── creating at .worktrees/content"
  git worktree add "$CONTENT_WORKTREE" content
fi
log "── content worktree ── pulling"
git -C "$CONTENT_WORKTREE" pull --ff-only

export CONTENT_ROOT="$CONTENT_WORKTREE"

# ─── Refetch: delete today's fetched items so fetch runs again ─────────────
# This must run BEFORE the --force block, since --force resets statuses based
# on rows that we're about to delete here.
if [ "$REFETCH" = true ]; then
  log "REFETCH: deleting today's fetched items so fetch runs again"
  uv run python - <<'PY'
import sqlite3
from datetime import datetime
from pathlib import Path

repo = Path.cwd()
db = repo / "state.db"
today = datetime.now().date().isoformat()

if not db.exists():
    print(f"no state.db at {db}; nothing to delete")
else:
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "DELETE FROM items WHERE first_seen_date = ?", (today,)
    )
    conn.commit()
    print(f"deleted {cur.rowcount} items first seen on {today}")
    conn.close()
PY
fi

# ─── Force: reset today's post-fetch state ─────────────────────────────────
if [ "$FORCE" = true ]; then
  log "FORCE: resetting today's post-fetch state"
  uv run python - <<'PY'
import sqlite3
from datetime import datetime
from pathlib import Path

repo = Path(__file__).resolve().parent if False else Path.cwd()
db = repo / "state.db"
today = datetime.now().date().isoformat()

conn = sqlite3.connect(db)
# Send today's processed items back to 'candidate' so rank/write can re-run.
# Leave 'new' alone (prefilter handles those naturally) and leave 'dropped'
# alone (those are deliberate negatives we don't want to re-evaluate).
# Match by first_seen_date — last_seen_date matches any prior-day published
# item that today's feeds re-surfaced (fetch bumps last_seen_date on conflict),
# which would re-promote already-published items into today's issue.
conn.execute(
    "UPDATE items SET status = 'candidate', score = NULL, tags = NULL, why = NULL "
    "WHERE status IN ('featured', 'appendix', 'published') AND first_seen_date = ?",
    (today,),
)
conn.execute("DELETE FROM runs WHERE date = ?", (today,))
conn.commit()
conn.close()

# Drop today's issue file so write.py runs.
issue = repo / "site" / "src" / "content" / "issues" / f"{today}.md"
if issue.exists():
    issue.unlink()
    print(f"removed {issue}")
PY
fi

# ─── Stages ────────────────────────────────────────────────────────────────
run_stage() {
  local label="$1"; shift
  CURRENT_STAGE="$label"
  log "── $label ── start"
  if "$@"; then
    log "── $label ── ok"
  else
    local rc=$?
    log "── $label ── FAILED (exit $rc)"
    return "$rc"
  fi
}

run_stage "fetch"     uv run python scripts/fetch.py
run_stage "prefilter" uv run python scripts/prefilter.py
run_stage "rank"      uv run python scripts/rank.py
run_stage "write"     uv run python scripts/write.py
run_stage "publish"   uv run python scripts/publish.py

# ─── Git commit/push ───────────────────────────────────────────────────────
# Stage everything that actually changed. We deliberately list paths rather
# than `git add -A` to avoid sweeping in untracked files (.venv, logs, etc.).
git_paths=(
  "site/src/content/issues"
  "state.db"
)

# Only stage paths that exist. (state.db is committed per spec; the issue dir
# may not exist on a brand-new clone before the first run.)
existing_paths=()
for p in "${git_paths[@]}"; do
  [ -e "$p" ] && existing_paths+=("$p")
done

if [ "${#existing_paths[@]}" -eq 0 ]; then
  log "git: nothing to commit (no tracked artifacts on disk)"
  exit 0
fi

git add "${existing_paths[@]}"
if git diff --cached --quiet; then
  log "git: nothing to commit"
  exit 0
fi

git commit -m "newsletter: $DATE_TODAY daily run" >/dev/null
log "git: committed $DATE_TODAY daily run"

# Push only if we have a remote — handy during local dev before the GitHub
# repo is wired up.
if git remote get-url origin >/dev/null 2>&1; then
  if git push --quiet; then
    log "git: pushed"
  else
    log "git: push failed (will retry on next run)"
    exit 1
  fi
else
  log "git: no remote configured; skipping push"
fi

log "DONE"
