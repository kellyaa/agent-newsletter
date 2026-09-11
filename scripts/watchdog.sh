#!/usr/bin/env bash
#
# Watchdog / self-heal for the daily newsletter run.
#
# Why this exists: on 2026-09-11 both scheduled agent sessions (11:00 generate,
# 12:00 post) died with a 503 from the calling agent's own model endpoint before
# they ran a single command. No issue was generated, and the 12:00 session —
# which is also the thing that reports failures — died the same way, so the
# failure went unreported. The pipeline itself was never the problem.
#
# Key fact this exploits: run.sh reaches its own model via LLM_BASE_URL and does
# NOT share the caller's endpoint. So once *any* session gets far enough to exec
# a shell command, the newsletter can still be produced.
#
# Contract: prints a single STATUS: line as its last line of stdout, so the
# calling agent session can report without re-deriving state.
#
#   STATUS: ALREADY_RAN <date>   — today's log exists and ended in DONE
#   STATUS: IN_PROGRESS <date>   — a run is mid-flight; not ours to touch
#   STATUS: HEALED <date>        — log was missing/incomplete; we ran it, it worked
#   STATUS: FAILED <date> <stage> — we ran it (or found it) and it failed
#
# Exit codes: 0 = ALREADY_RAN, IN_PROGRESS, or HEALED; 1 = FAILED.
#
# Usage:
#   ./scripts/watchdog.sh            — check, and run the pipeline if needed
#   ./scripts/watchdog.sh --check    — check only, never run the pipeline
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CHECK_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=true ;;
    -h|--help) sed -n '3,30p' "$0"; exit 0 ;;
    *) printf 'unknown arg: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

DATE_TODAY="$(date +%F)"
LOG_FILE="$REPO_ROOT/logs/run-$DATE_TODAY.log"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
say() { printf '%s [watchdog] %s\n' "$(ts)" "$*"; }

# Which stage did a log die at? Echoes a stage name, or "unknown".
failed_stage() {
  local f="$1"
  grep -oE 'FAILED at stage=[a-z]+' "$f" 2>/dev/null | tail -1 | cut -d= -f2 \
    || echo unknown
}

# A run is complete iff its log's last DONE marker is present.
log_is_complete() {
  [ -f "$1" ] && grep -q '^\S* \[run\] DONE$' "$1"
}

if log_is_complete "$LOG_FILE"; then
  say "today's run already completed ($LOG_FILE)"
  echo "STATUS: ALREADY_RAN $DATE_TODAY"
  exit 0
fi

# Two DIFFERENT locks, deliberately. RUN_LOCK belongs to run.sh; we only ever
# *probe* it, never hold it — holding it would lock out the very run we're
# trying to start. WD_LOCK is ours, for watchdog-vs-watchdog exclusion.
RUN_LOCK="$REPO_ROOT/.watchdog.lock"
WD_LOCK="$REPO_ROOT/.watchdog-self.lock"

run_in_flight() {
  # Returns 0 if some other process holds the run lock (i.e. a run is live).
  # The subshell's fd is closed on return, so this never retains the lock.
  ( exec 9>"$RUN_LOCK"; flock -n 9 ) && return 1 || return 0
}

if [ -f "$LOG_FILE" ]; then
  if run_in_flight; then
    say "a run is currently in flight (lock held); leaving it alone"
    echo "STATUS: IN_PROGRESS $DATE_TODAY"
    exit 0
  fi
  stage="$(failed_stage "$LOG_FILE")"
  say "today's log exists but has no DONE marker; last failure stage=${stage:-unknown}"
else
  stage=""
  say "no log at $LOG_FILE — the scheduled run never executed"
fi

if [ "$CHECK_ONLY" = true ]; then
  say "--check given; not running the pipeline"
  echo "STATUS: FAILED $DATE_TODAY ${stage:-never-started}"
  exit 1
fi

# Guard against two watchdogs racing (e.g. the poster and the late watchdog
# firing close together). This is our OWN lock, not run.sh's — see above.
exec 8>"$WD_LOCK"
if ! flock -n 8; then
  say "another watchdog/run holds the lock; standing down"
  echo "STATUS: FAILED $DATE_TODAY concurrent-run"
  exit 1
fi

say "self-healing: invoking run.sh"
if bash "$REPO_ROOT/run.sh"; then
  if log_is_complete "$LOG_FILE"; then
    say "run.sh completed"
    echo "STATUS: HEALED $DATE_TODAY"
    exit 0
  fi
  # run.sh exited 0 without a DONE marker. That is NOT a success: it's how a
  # lockout ("another run holds the lock") and an early "nothing to commit"
  # exit both look. Reporting HEALED here would recreate exactly the silent
  # false-success this watchdog exists to catch, so treat it as a failure and
  # let a human look at the log.
  say "run.sh exited 0 but printed no DONE marker — NOT treating as success"
  echo "STATUS: FAILED $DATE_TODAY no-done-marker"
  exit 1
fi

stage="$(failed_stage "$LOG_FILE")"
say "run.sh failed at stage=${stage:-unknown}"
echo "STATUS: FAILED $DATE_TODAY ${stage:-unknown}"
exit 1
