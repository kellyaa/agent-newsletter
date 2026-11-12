# shellcheck shell=bash
#
# The env-file contract, sourced by run.sh, the root watchdog.sh and
# scripts/watchdog.sh. This is its only home
# (tests/test_env_files_single_source.py enforces that).
#
# Operator settings (LLM endpoint and keys, STALE_HOURS, WATCHDOG_WEBHOOK_URL,
# NEWSLETTER_PYTHON, ...) live in two optional files, sourced in this order with
# `set -a`, so the LAST file wins for any given key and every value is exported:
#   1. ~/.config/agent-newsletter/env   machine-wide defaults
#   2. <repo-root>/.env                  repo-local, gitignored (overrides)
#
# Callers load them FIRST, before anything reads one of those settings: before
# run.sh's lock alert and runner choice, before the root watchdog reads
# STALE_HOURS or alerts, and before scripts/watchdog.sh picks its runner. Only
# run.sh used to load them, and late, so a value set only in .env was ignored
# by both watchdogs and by run.sh's earliest steps.

# envfiles_load <repo-root>   source every env file that exists, in order.
# With HOME unset (a bare cron/launchd environment) the machine-wide file is
# skipped rather than tripping `set -u` in the caller.
envfiles_load() {
  local f
  for f in "${HOME:+$HOME/.config/agent-newsletter/env}" "$1/.env"; do
    if [ -n "$f" ] && [ -f "$f" ]; then
      set -a
      # shellcheck disable=SC1090
      . "$f"
      set +a
    fi
  done
}
