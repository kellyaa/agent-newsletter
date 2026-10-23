#!/usr/bin/env bash
#
# Run ONE edition's pipeline: fetch → prefilter → rank → write → publish, then
# commit and push that edition's folder on the content branch.
#
# Normally started by run.sh, as a child process per edition, so that `set -e`,
# the ERR trap, the log and the environment are per edition and one edition's
# failure cannot take the others down. It can also be run by hand:
#
#   EDITION=agents CONTENT_WORKTREE=.worktrees/content CONTENT_ROOT=.worktrees/content \
#     scripts/run_edition.sh [--force | --refetch]
#
# Every stage is idempotent; a re-run picks up where a prior failure left off:
#   - fetch skips if today's items are already in the DB
#   - prefilter is a no-op if there's nothing new to gate
#   - rank skips items that already have a status past 'candidate'
#   - write skips if today's issue file exists
#   - publish skips if today's runs row exists and items are published
#
#   --force    reset today's post-fetch state of THIS edition and re-run
#              everything downstream of fetch (arXiv is rate-limited, so fetched
#              data is kept)
#   --refetch  also delete today's fetched items so fetch runs again. Implies
#              --force.
#
# Environment (set by run.sh; required when run by hand):
#   EDITION            edition slug (editions/<slug>/)
#   CONTENT_ROOT       content worktree (db.py reads it)
#   CONTENT_WORKTREE   the same worktree, for the git commands here
#   NEWSLETTER_PYTHON  python command, e.g. "uv run python" (default: python3)
#   RUN_DATE           date being produced (default: today)
#
# Log: logs/<slug>/run-DATE.log; a successful run ends with a "[run] DONE" line.
# On failure: a notification naming the edition, a failed run_attempts row, and
# the edition's state.db pushed to content; exit status is non-zero.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FORCE=false
REFETCH=false
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    --refetch) REFETCH=true; FORCE=true ;;
    -h|--help) sed -n '3,36p' "$0"; exit 0 ;;
    *) printf 'unknown arg: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

: "${EDITION:?EDITION must be set (run.sh does this; there is no default edition)}"
: "${CONTENT_WORKTREE:?CONTENT_WORKTREE must be set (run.sh does this)}"
export CONTENT_ROOT="${CONTENT_ROOT:-$CONTENT_WORKTREE}"
export EDITION

read -r -a PY_RUN <<< "${NEWSLETTER_PYTHON:-python3}"

DATE_TODAY="${RUN_DATE:-$(date +%F)}"
LOG_DIR="$REPO_ROOT/logs/$EDITION"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-$DATE_TODAY.log"

# tee everything through to a per-edition, per-day log so we can grep failures later.
exec > >(tee -a "$LOG_FILE") 2>&1

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s [run] %s\n' "$(ts)" "$*"; }

# Run accounting (#13). llm.py appends one line per LLM call, tagged with
# RUN_ATTEMPT; the file is per edition and day and never truncated, so
# publish.py's runs row counts every attempt's spend and run_attempt.py counts
# this attempt's.
export LLM_USAGE_LOG="$LOG_DIR/usage-$DATE_TODAY.jsonl"
export RUN_STARTED_AT="$(ts)"
export RUN_DATE="$DATE_TODAY"
RUN_ATTEMPT=""
CURRENT_STAGE="config"
EDITION_NAME="$EDITION"

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
  notify "$EDITION_NAME failed" \
    "stage=$stage rc=$rc — re-run: ./run.sh --failed (log: logs/$EDITION/$(basename "$LOG_FILE"))"
  record_failed_attempt "$stage" "$rc"
  exit "$rc"
}

# Mark this attempt failed in the edition's state.db and push state.db (never
# the issue file) to content, so the failure is visible off the pod and the next
# attempt starts from this one's state. Best effort: every step may fail without
# masking the original error. A no-op until the attempt row exists, i.e. for
# failures before the content worktree is ready.
record_failed_attempt() {
  [ -n "$RUN_ATTEMPT" ] || return 0
  set +e
  "${PY_RUN[@]}" scripts/run_attempt.py finish "$RUN_ATTEMPT" failed \
    --stage "$1" --exit-code "$2"
  # `commit -- <path>` commits that path only, even if the issue file was
  # already staged by the commit step below.
  git -C "$CONTENT_WORKTREE" add "editions/$EDITION/state.db" \
    && git -C "$CONTENT_WORKTREE" commit -qm \
      "newsletter($EDITION): $DATE_TODAY attempt $RUN_ATTEMPT failed at $1" \
      -- "editions/$EDITION/state.db" \
    && git -C "$CONTENT_WORKTREE" push --quiet \
    && log "git: pushed failed attempt $RUN_ATTEMPT to content"
  set -e
}
trap fail_handler ERR

log "── edition ── $EDITION (date $DATE_TODAY)"

# Validate the edition's config before any stage does real work. A bad config
# stops this edition here, not halfway through rank.
EDITION_NAME="$("${PY_RUN[@]}" scripts/edition.py name)"
log "── edition ── $EDITION_NAME"

# ─── Run attempt ───────────────────────────────────────────────────────────
# One run_attempts row per invocation (scripts/run_attempt.py), in this
# edition's own state.db. Accounting must not sink the run, so a failure here
# just skips the row.
RUN_ATTEMPT="$("${PY_RUN[@]}" scripts/run_attempt.py start)" || RUN_ATTEMPT=""
[[ "$RUN_ATTEMPT" =~ ^[0-9]+$ ]] || RUN_ATTEMPT=""
export RUN_ATTEMPT
log "── run attempt ── ${RUN_ATTEMPT:-not recorded}"

# ─── Refetch / force: reset today's state for THIS edition ─────────────────
if [ "$REFETCH" = true ]; then
  CURRENT_STAGE="reset"
  log "REFETCH: deleting today's fetched items so fetch runs again"
  "${PY_RUN[@]}" scripts/reset_day.py --refetch
elif [ "$FORCE" = true ]; then
  CURRENT_STAGE="reset"
  log "FORCE: resetting today's post-fetch state"
  "${PY_RUN[@]}" scripts/reset_day.py
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

run_stage "fetch"     "${PY_RUN[@]}" scripts/fetch.py
run_stage "prefilter" "${PY_RUN[@]}" scripts/prefilter.py
run_stage "rank"      "${PY_RUN[@]}" scripts/rank.py
run_stage "write"     "${PY_RUN[@]}" scripts/write.py
run_stage "publish"   "${PY_RUN[@]}" scripts/publish.py

# Mark the attempt ok before committing, so the commit below carries the row.
# A git add/commit error re-marks it failed at stage=git. A failed push does
# not: the issue is committed locally and goes out with the next push.
CURRENT_STAGE="git"
if [ -n "$RUN_ATTEMPT" ]; then
  "${PY_RUN[@]}" scripts/run_attempt.py finish "$RUN_ATTEMPT" ok \
    || log "run attempt: could not mark $RUN_ATTEMPT ok"
fi

# ─── Git commit/push (content branch) ──────────────────────────────────────
# Commit and push this edition's folder (state.db + issues) as soon as it is
# done: its issue goes live without waiting for the other editions, and a later
# failure elsewhere can't hold it back. All git ops run with -C so we don't
# touch the main working tree's index.
git -C "$CONTENT_WORKTREE" add "editions/$EDITION"
if git -C "$CONTENT_WORKTREE" diff --cached --quiet; then
  log "git: nothing to commit"
  log "DONE"
  exit 0
fi

git -C "$CONTENT_WORKTREE" commit -m "newsletter($EDITION): $DATE_TODAY daily run" >/dev/null
log "git: committed $DATE_TODAY daily run (content, $EDITION)"

if git -C "$CONTENT_WORKTREE" remote get-url origin >/dev/null 2>&1; then
  if git -C "$CONTENT_WORKTREE" push --quiet; then
    log "git: pushed to content"
  else
    log "git: push failed (will retry on next run)"
    exit 1
  fi
else
  log "git: no remote configured; skipping push"
fi

log "DONE"
