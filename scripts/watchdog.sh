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
    -h|--help) sed -n '3,34p' "$0"; exit 0 ;;
    *) printf 'unknown arg: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

DATE_TODAY="$(date +%F)"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
say() { printf '%s [watchdog] %s\n' "$(ts)" "$*"; }

# Python for reading the edition list. Same preference as run.sh, minus the long
# uv probe: NEWSLETTER_PYTHON, else the committed venv, else python3.
if [ -n "${NEWSLETTER_PYTHON:-}" ]; then
  read -r -a PY_RUN <<< "$NEWSLETTER_PYTHON"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY_RUN=("$REPO_ROOT/.venv/bin/python")
else
  PY_RUN=(python3)
fi

# Editions to check: every draft/live one (paused ones are skipped). An edition
# whose config is invalid counts as one to check, so it surfaces as FAILED.
SLUGS=()
while IFS=$'\t' read -r slug status _name; do
  [ -n "$slug" ] || continue
  [ "$status" = paused ] && continue
  SLUGS+=("$slug")
done < <("${PY_RUN[@]}" scripts/edition.py list 2>/dev/null)

if [ "${#SLUGS[@]}" -eq 0 ]; then
  say "no draft/live editions found (is scripts/edition.py list working?)"
  echo "STATUS: FAILED $DATE_TODAY no-editions"
  exit 1
fi

log_file() { echo "$REPO_ROOT/logs/$1/run-$DATE_TODAY.log"; }

# Which stage did a log die at? Echoes a stage name, or "unknown".
failed_stage() {
  local f="$1"
  grep -oE 'FAILED at stage=[a-z-]+' "$f" 2>/dev/null | tail -1 | cut -d= -f2 \
    || echo unknown
}

# A run is complete iff its log's last DONE marker is present.
log_is_complete() {
  [ -f "$1" ] && grep -q '^\S* \[run\] DONE$' "$1"
}

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

# Which editions still need a run, and (if their log exists) where they died.
NEED=()
declare -A STAGE_OF=()
DONE_SLUGS=()
for slug in "${SLUGS[@]}"; do
  lf="$(log_file "$slug")"
  if log_is_complete "$lf"; then
    say "$slug: today's run already completed ($lf)"
    DONE_SLUGS+=("$slug")
  else
    NEED+=("$slug")
    if [ -f "$lf" ]; then
      STAGE_OF["$slug"]="$(failed_stage "$lf")"
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

if run_in_flight; then
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
exec 8>"$WD_LOCK"
if ! flock -n 8; then
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
  if log_is_complete "$lf"; then
    HEALED+=("$s")
  else
    if [ -f "$lf" ]; then
      STAGE_OF["$s"]="$(failed_stage "$lf")"
      # A log with no FAILED marker and no DONE did not fail in a stage.
      grep -q 'FAILED at stage=' "$lf" || STAGE_OF["$s"]="no-done-marker"
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
