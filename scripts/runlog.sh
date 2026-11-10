# shellcheck shell=bash
#
# The run-log contract, sourced by run.sh, scripts/run_edition.sh and
# scripts/watchdog.sh. This is its only home (tests/test_run_log_single_source.py
# enforces that).
#
# run_edition.sh writes one log per edition and day, logs/<slug>/run-<date>.log.
# A run is complete iff that log has a `<ts> [run] DONE` line. A failed run
# has a `<ts> [run] FAILED at stage=<stage> rc=<rc>` line. run.sh and
# watchdog.sh read these markers back to decide what is done, what failed (and
# where), and what never ran. Change the writer and the readers here, together.
#
# Stage names (CURRENT_STAGE) must match RUNLOG_STAGE_RE, or the readers cannot
# recover them.

RUNLOG_STAGE_RE='[a-z-]+'

# UTC instant used to prefix every log line (also exported as RUN_STARTED_AT).
ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }

# One run-log line: `<ts> [run] <message>`.
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

# ─── Paths ─────────────────────────────────────────────────────────────────
# runlog_dir <repo-root> [<slug>]   logs/ (orchestrator) or logs/<slug>/ (edition)
runlog_dir() { printf '%s\n' "$1/logs${2:+/$2}"; }

# runlog_path <repo-root> <slug> <date>   an edition's log for one day
runlog_path() { printf '%s\n' "$(runlog_dir "$1" "$2")/run-$3.log"; }

# runlog_summary_path <repo-root> <date>  run.sh's orchestrator summary log
runlog_summary_path() { printf '%s\n' "$(runlog_dir "$1")/run-$2.log"; }

# ─── Writer side ───────────────────────────────────────────────────────────
# runlog_mark_done
runlog_mark_done() { log "DONE"; }

# runlog_mark_failed <stage> <rc> [<suffix>]
runlog_mark_failed() { log "FAILED at stage=$1 rc=$2${3:+ $3}"; }

# ─── Reader side ───────────────────────────────────────────────────────────
# runlog_is_complete <file>   0 iff the log exists and has the DONE marker
runlog_is_complete() {
  [ -f "$1" ] && grep -q '^\S* \[run\] DONE$' "$1"
}

# runlog_failed_stage <file>   print the last failed stage; 1 if there is none
runlog_failed_stage() {
  local stage
  stage="$(grep -oE "FAILED at stage=$RUNLOG_STAGE_RE" "$1" 2>/dev/null | tail -1 | cut -d= -f2)" \
    || true
  [ -n "$stage" ] || return 1
  printf '%s\n' "$stage"
}
