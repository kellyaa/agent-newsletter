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
# Contract: prints one EDITION: line per draft/live edition, then a single
# STATUS: line as the LAST line of stdout (worst-of across editions), so the
# calling agent session can report without re-deriving state.
#
#   EDITION: <slug> ALREADY_RAN <date>        — its log exists and ended in DONE
#   EDITION: <slug> IN_PROGRESS <date>        — a run is mid-flight; not ours to touch
#   EDITION: <slug> HEALED <date>             — missing/incomplete; we ran it, it worked
#   EDITION: <slug> FAILED <date> <stage>     — we ran it (or found it) and it failed
#
#   STATUS: ALREADY_RAN <date>   — every edition's log ended in DONE
#   STATUS: IN_PROGRESS <date>   — a run is mid-flight; not ours to touch
#   STATUS: HEALED <date>        — at least one edition was missing/incomplete; we ran it
#   STATUS: FAILED <date> <slug>:<stage>[ <slug>:<stage>...]
#                                — names each failed edition and its stage
#   STATUS: FAILED <date> no-flock — flock(1) is missing, so no run can take
#                                the lock (scripts/runlock.sh, #662)
#   STATUS: FAILED <date> edition-list — scripts/edition.py list itself failed
#                                (scripts/editionlist.sh, #670)
#   STATUS: FAILED <date> no-editions  — the list worked but had no draft/live edition
#
# Before those lines it also prints one `MISSED: <slug> <date>` line per recent
# day on which a draft/live edition has no run_attempts row at all (run.sh never
# started, so there is no log to heal; scripts/missed_days.py, issue #519). It is
# informational: it does not change STATUS or the exit code, and works off macOS.
# Likewise one `DEAD: <slug>/<source> no items for N days` line per source whose
# recorded fetches all came back empty or failed for N days (default 7, or the
# source's `dead_after_days` in sources.yaml; scripts/dead_sources.py, #572).
#
# If either reader itself fails (crash, unexpected output), one
# `CHECK-FAILED: <reader> rc=<n>: <last stderr line>` line says so instead of
# the check reading as "nothing found" (#673); also informational.
#
# Paused editions are not checked. Per-edition logs are logs/<slug>/run-DATE.log
# (written by scripts/run_edition.sh); a run is complete iff its log has DONE.
#
# Exit codes: 0 = ALREADY_RAN, IN_PROGRESS, or HEALED; 1 = FAILED.
#
# Usage:
#   ./scripts/watchdog.sh            — check, and run the missing/failed editions
#   ./scripts/watchdog.sh --check    — check only, never run the pipeline
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CHECK_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=true ;;
    -h|--help) awk 'NR>=3 { if (/^#/) print; else exit }' "$0"; exit 0 ;;
    *) printf 'unknown arg: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

DATE_TODAY="$(date +%F)"

# ts and the run-log path/marker readers (the run-log contract).
# shellcheck source=scripts/runlog.sh
. "$REPO_ROOT/scripts/runlog.sh"
# The run/self lock paths and the flock helpers (the single-run lock contract).
# shellcheck source=scripts/runlock.sh
. "$REPO_ROOT/scripts/runlock.sh"
# The content worktree path (the content-branch contract).
# shellcheck source=scripts/contentbranch.sh
. "$REPO_ROOT/scripts/contentbranch.sh"

say() { printf '%s [watchdog] %s\n' "$(ts)" "$*"; }

# The operator's env files (scripts/envfiles.sh), as run.sh loads them, so a
# NEWSLETTER_PYTHON set there picks the same runner here as in run.sh.
# shellcheck source=scripts/envfiles.sh
. "$REPO_ROOT/scripts/envfiles.sh"
envfiles_load "$REPO_ROOT"

# Python for the edition list and the readers: NEWSLETTER_PYTHON, else the
# committed venv, else python3 (the "check" mode of scripts/pyrunner.sh: no uv
# probe, so the hourly check never waits on a hung uv).
# shellcheck source=scripts/pyrunner.sh
. "$REPO_ROOT/scripts/pyrunner.sh"
pyrunner_select "$REPO_ROOT" check

# Editions to check: every draft/live one (paused ones are skipped). An edition
# whose config is invalid counts as one to check, so it surfaces as FAILED. The
# list contract lives in scripts/editionlist.sh; a failing `edition.py list` is
# its own FAILED reason, not "no editions" (#670).
# shellcheck source=scripts/editionlist.sh
. "$REPO_ROOT/scripts/editionlist.sh"
if ! editionlist_load "${PY_RUN[@]}"; then
  say "scripts/edition.py list failed; cannot tell which editions to check"
  echo "STATUS: FAILED $DATE_TODAY edition-list"
  exit 1
fi
SLUGS=()
for slug in ${EDITION_SLUGS[@]+"${EDITION_SLUGS[@]}"}; do
  editionlist_is_paused "${EDITION_STATUS_OF[$slug]}" && continue
  SLUGS+=("$slug")
done

if [ "${#SLUGS[@]}" -eq 0 ]; then
  say "no draft/live editions found"
  echo "STATUS: FAILED $DATE_TODAY no-editions"
  exit 1
fi

# Days with no run_attempts row (run never started). Reads the content worktree
# when there is one, as run.sh does. Never affects STATUS or the exit code.
CONTENT_ROOT="${CONTENT_ROOT:-}"
CONTENT_WT="$(content_worktree_path "$REPO_ROOT")"
if [ -z "$CONTENT_ROOT" ] && [ -e "$CONTENT_WT/.git" ]; then
  CONTENT_ROOT="$CONTENT_WT"
fi
export CONTENT_ROOT
# run_reader <script> <prefix>: print the reader's stdout into READER_OUT. Each
# reader exits 0 with no lines, or 1 with only <prefix> lines. Anything else
# (a crash: rc 1 and no lines, or any other rc) is a failed check, reported
# as one CHECK-FAILED: line so it cannot read as "nothing found" (#673).
# Still informational: STATUS and the exit code are unaffected.
run_reader() {
  local script="$1" prefix="$2" err rc=0 line
  err="$(mktemp)"
  READER_OUT="$("${PY_RUN[@]}" "scripts/$script" 2>"$err")" || rc=$?
  local ok=true
  case "$rc" in
    0) [ -z "$READER_OUT" ] || ok=false ;;
    1)
      [ -n "$READER_OUT" ] || ok=false
      while IFS= read -r line; do
        [[ "$line" == "$prefix "* ]] || ok=false
      done <<< "$READER_OUT"
      ;;
    *) ok=false ;;
  esac
  if [ "$ok" != true ]; then
    line="$(grep -v '^[[:space:]]*$' "$err" | tail -1)"
    echo "CHECK-FAILED: $script rc=$rc: ${line:-no error output}"
    READER_OUT=""
  fi
  rm -f "$err"
}

run_reader missed_days.py MISSED:
MISSED_OUT="$READER_OUT"
if [ -n "$MISSED_OUT" ]; then
  say "days with no run_attempts row (the scheduled run never started):"
  printf '%s\n' "$MISSED_OUT"
fi

# Sources whose fetches have come back empty for days. Same read-only,
# informational contract as the MISSED: lines above.
run_reader dead_sources.py DEAD:
DEAD_OUT="$READER_OUT"
if [ -n "$DEAD_OUT" ]; then
  say "sources with no items for days (silently dead feeds):"
  printf '%s\n' "$DEAD_OUT"
fi

log_file() { runlog_path "$REPO_ROOT" "$1" "$DATE_TODAY"; }

# Two DIFFERENT locks, deliberately. RUN_LOCK belongs to run.sh; we only ever
# *probe* it, never hold it — holding it would lock out the very run we're
# trying to start. WD_LOCK is ours, for watchdog-vs-watchdog exclusion.
RUN_LOCK="$(runlock_run_path "$REPO_ROOT")"
WD_LOCK="$(runlock_self_path "$REPO_ROOT")"

# Without flock neither lock can be probed or taken, and no run.sh can start
# either. Say so instead of guessing "in flight" (IN_PROGRESS forever, exit 0).
no_flock() {
  say "flock not found; cannot probe or take the run lock, and run.sh cannot run — install flock (util-linux)"
  echo "STATUS: FAILED $DATE_TODAY no-flock"
  exit 1
}

# Which editions still need a run, and (if their log exists) where they died.
NEED=()
declare -A STAGE_OF=()
DONE_SLUGS=()
for slug in "${SLUGS[@]}"; do
  lf="$(log_file "$slug")"
  if runlog_is_complete "$lf"; then
    say "$slug: today's run already completed ($lf)"
    DONE_SLUGS+=("$slug")
  else
    NEED+=("$slug")
    if [ -f "$lf" ]; then
      STAGE_OF["$slug"]="$(runlog_failed_stage "$lf" || echo unknown)"
      say "$slug: log exists but has no DONE marker; last failure stage=${STAGE_OF[$slug]:-unknown}"
    else
      STAGE_OF["$slug"]=""
      say "$slug: no log at $lf — the scheduled run never executed it"
    fi
  fi
done

emit_done() {
  local s
  for s in "${DONE_SLUGS[@]}"; do echo "EDITION: $s ALREADY_RAN $DATE_TODAY"; done
}

if [ "${#NEED[@]}" -eq 0 ]; then
  emit_done
  echo "STATUS: ALREADY_RAN $DATE_TODAY"
  exit 0
fi

lock_rc=0
runlock_held "$RUN_LOCK" || lock_rc=$?
[ "$lock_rc" -eq "$RUNLOCK_NO_FLOCK" ] && no_flock
if [ "$lock_rc" -eq 0 ]; then
  say "a run is currently in flight (lock held); leaving it alone"
  emit_done
  for s in "${NEED[@]}"; do echo "EDITION: $s IN_PROGRESS $DATE_TODAY"; done
  echo "STATUS: IN_PROGRESS $DATE_TODAY"
  exit 0
fi

# Worst-of summary of FAILED editions: "slug:stage slug:stage".
failed_summary() {
  local out="" s
  for s in "$@"; do out+="${out:+ }$s:${STAGE_OF[$s]:-never-started}"; done
  printf '%s' "$out"
}

if [ "$CHECK_ONLY" = true ]; then
  say "--check given; not running the pipeline"
  emit_done
  for s in "${NEED[@]}"; do echo "EDITION: $s FAILED $DATE_TODAY ${STAGE_OF[$s]:-never-started}"; done
  echo "STATUS: FAILED $DATE_TODAY $(failed_summary "${NEED[@]}")"
  exit 1
fi

# Guard against two watchdogs racing (e.g. the poster and the late watchdog
# firing close together). This is our OWN lock, not run.sh's — see above.
lock_rc=0
runlock_acquire 8 "$WD_LOCK" || lock_rc=$?
[ "$lock_rc" -eq "$RUNLOCK_NO_FLOCK" ] && no_flock
if [ "$lock_rc" -ne 0 ]; then
  say "another watchdog/run holds the lock; standing down"
  echo "STATUS: FAILED $DATE_TODAY concurrent-run"
  exit 1
fi

say "self-healing: invoking run.sh for: ${NEED[*]}"
edition_args=()
for s in "${NEED[@]}"; do edition_args+=(--edition "$s"); done
bash "$REPO_ROOT/run.sh" "${edition_args[@]}"
run_rc=$?

# Judge each edition by its own log, not by run.sh's exit code alone: run.sh
# exits 0 on a lockout ("another run holds the lock") without running anything,
# which would otherwise recreate exactly the silent false-success this watchdog
# exists to catch.
FAILED=()
HEALED=()
for s in "${NEED[@]}"; do
  lf="$(log_file "$s")"
  if runlog_is_complete "$lf"; then
    HEALED+=("$s")
  else
    if [ -f "$lf" ]; then
      # A log with no FAILED marker and no DONE did not fail in a stage.
      STAGE_OF["$s"]="$(runlog_failed_stage "$lf" || echo no-done-marker)"
    else
      STAGE_OF["$s"]="no-done-marker"
    fi
    FAILED+=("$s")
  fi
done

emit_done
for s in "${HEALED[@]}"; do echo "EDITION: $s HEALED $DATE_TODAY"; done
for s in "${FAILED[@]}"; do echo "EDITION: $s FAILED $DATE_TODAY ${STAGE_OF[$s]}"; done

if [ "${#FAILED[@]}" -gt 0 ]; then
  say "run.sh rc=$run_rc; failed editions: $(failed_summary "${FAILED[@]}")"
  echo "STATUS: FAILED $DATE_TODAY $(failed_summary "${FAILED[@]}")"
  exit 1
fi
say "run.sh completed"
echo "STATUS: HEALED $DATE_TODAY"
exit 0
