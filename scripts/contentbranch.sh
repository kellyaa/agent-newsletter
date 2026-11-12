# shellcheck shell=bash
#
# The content-branch contract the shell scripts share, sourced by run.sh,
# scripts/run_edition.sh, scripts/watchdog.sh and the root watchdog.sh. This is
# its only home (tests/test_content_branch_single_source.py enforces that).
#
# run.sh creates the `content` worktree at content_worktree_path and
# run_edition.sh commits each edition's folder there with content_run_subject
# (or content_failed_subject when an attempt fails). The readers find them
# again: the root watchdog.sh measures staleness from the newest commit whose
# subject matches CONTENT_DAILY_RUN_RE, scripts/watchdog.sh points CONTENT_ROOT
# at the worktree for missed_days.py / dead_sources.py, and backfill.py matches
# the subject prefix via models.run_commit_prefix. Change the writer and the
# readers here, together.

# Matches a successful daily-run subject, per edition ("newsletter(<slug>): ")
# or from before the multi-edition layout ("newsletter: "). Failed-attempt and
# persist subjects must not match: they must not reset the staleness clock.
CONTENT_DAILY_RUN_RE='^newsletter(\([a-z0-9-]+\))?:.* daily run$'

# content_worktree_path <repo-root>   where run.sh checks out the content branch
content_worktree_path() { printf '%s\n' "$1/.worktrees/content"; }

# content_run_subject <slug> <date>   subject of a successful daily-run commit
content_run_subject() { printf 'newsletter(%s): %s daily run\n' "$1" "$2"; }

# content_failed_subject <slug> <date> <attempt> <stage>
#   subject of the state.db commit pushed when an attempt fails
content_failed_subject() {
  printf 'newsletter(%s): %s attempt %s failed at %s\n' "$1" "$2" "$3" "$4"
}
