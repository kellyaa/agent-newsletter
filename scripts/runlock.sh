# shellcheck shell=bash
#
# The single-run lock contract, sourced by run.sh and scripts/watchdog.sh. This
# is its only home (tests/test_run_lock_single_source.py enforces that).
#
# run.sh holds the run lock for the life of a run (editions run one after
# another under it; children inherit the descriptor). scripts/watchdog.sh only
# ever *probes* it, to tell "died mid-run" from "still running", and holds its
# own self lock for watchdog-vs-watchdog exclusion. Both are advisory flock(1)
# locks on gitignored files under the repo root, released when the holder exits.
#
# Every helper returns 2 when flock(1) is unavailable (stock macOS ships none).
# Callers must treat that as a failure: a missing flock used to read as "lock
# held", so run.sh exited 0 without running and the watchdog reported a run in
# flight that never finished (#662).

RUNLOCK_NO_FLOCK=2

# runlock_run_path <repo-root>    run.sh's lock (.watchdog.lock, a legacy name)
runlock_run_path() { printf '%s\n' "$1/.watchdog.lock"; }

# runlock_self_path <repo-root>   scripts/watchdog.sh's own lock
runlock_self_path() { printf '%s\n' "$1/.watchdog-self.lock"; }

# runlock_available   0 iff flock(1) is on PATH
runlock_available() { command -v flock >/dev/null 2>&1; }

# runlock_acquire <fd> <file>
#   Open <file> on descriptor <fd> (one digit) in the calling shell and take
#   the lock without waiting. 0 = acquired (held until the shell exits),
#   1 = another process holds it, 2 = flock unavailable (nothing is opened).
runlock_acquire() {
  [[ "$1" =~ ^[0-9]$ ]] || { printf 'runlock_acquire: bad fd %s\n' "$1" >&2; return 64; }
  runlock_available || return "$RUNLOCK_NO_FLOCK"
  eval "exec $1>\"\$2\""
  flock -n "$1" && return 0
  return 1
}

# runlock_held <file>
#   Probe without retaining: 0 = another process holds it, 1 = free,
#   2 = flock unavailable. The subshell's descriptor closes on return.
runlock_held() {
  runlock_available || return "$RUNLOCK_NO_FLOCK"
  if ( exec 9>"$1"; flock -n 9 ); then return 1; fi
  return 0
}
