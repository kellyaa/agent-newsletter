#!/usr/bin/env bash
#
# Watchdog: fires a notification (macOS osascript, Linux notify-send, and/or a
# WATCHDOG_WEBHOOK_URL POST) if the newsletter pipeline appears stuck.
#
# Triggered hourly by ~/Library/LaunchAgents/com.kelly.agent-newsletter-watchdog.plist.
# Definition of "stuck": the newest successful daily-run commit
# ("newsletter(<slug>): <date> daily run", or the pre-multi-edition
# "newsletter: <date> daily run") of ANY edition on the `content` branch is older
# than STALE_HOURS hours. Per-edition health is scripts/watchdog.sh's job. Failure/recovery commits on that branch do not count.
# If there is no content branch yet, fall back to main's HEAD, and only when it
# is a `newsletter:` commit (so hand-editing code never false-fires).
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

STALE_HOURS="${STALE_HOURS:-36}"
STATE_FILE="$REPO_ROOT/logs/watchdog-last-fire"
mkdir -p "$REPO_ROOT/logs"

# notify (macOS osascript, else Linux notify-send, plus the optional
# WATCHDOG_WEBHOOK_URL POST) is shared with the pipeline's failure alerts.
# shellcheck source=scripts/runlog.sh
. "$REPO_ROOT/scripts/runlog.sh"
# The content worktree path and the daily-run subject regex are shared with
# run.sh / run_edition.sh, which create the worktree and write the commits.
# shellcheck source=scripts/contentbranch.sh
. "$REPO_ROOT/scripts/contentbranch.sh"

# Where the pipeline's commits live. run.sh commits every daily run to the
# `content` branch through this worktree, so they never appear in main's
# `git log` -- checking only main's HEAD (as this script once did) always hit
# the "not a newsletter commit" skip and could never fire.
CONTENT_WORKTREE="${CONTENT_WORKTREE:-$(content_worktree_path "$REPO_ROOT")}"

# A successful daily run is committed as "newsletter(<slug>): <date> daily run"
# (before the multi-edition layout: "newsletter: <date> daily run"); only those
# match CONTENT_DAILY_RUN_RE. Other `newsletter` subjects (e.g. "... attempt N
# failed at <stage>", "persist state.db from interrupted ... run") are written
# when a run FAILS, so they must not reset the staleness clock.

# Newest daily-run commit on the content branch (epoch seconds, 0 if none).
# Prefer the live worktree; otherwise look at the local and remote-tracking
# content refs (a fresh clone, or a pipeline running on another machine).
content_epoch=0
if [ -e "$CONTENT_WORKTREE/.git" ]; then
  content_epoch="$(git -C "$CONTENT_WORKTREE" log -1 --format=%ct -E \
    --grep="$CONTENT_DAILY_RUN_RE" 2>/dev/null || true)"
else
  refs=()
  for r in refs/heads/content refs/remotes/origin/content; do
    git rev-parse --verify -q "$r" >/dev/null 2>&1 && refs+=("$r")
  done
  if [ "${#refs[@]}" -gt 0 ]; then
    content_epoch="$(git log "${refs[@]}" --format=%ct -E \
      --grep="$CONTENT_DAILY_RUN_RE" 2>/dev/null | sort -rn | head -1 || true)"
  fi
fi
content_epoch="${content_epoch:-0}"

now_epoch="$(date -u +%s)"

if [ "$content_epoch" -gt 0 ]; then
  last_commit_epoch="$content_epoch"
  echo "watchdog: using newest daily-run commit on the content branch"
else
  # No content-branch history at all (legacy single-branch checkout, or a repo
  # that has never run the pipeline): fall back to the original HEAD check.
  last_commit_epoch="$(git log -1 --format=%ct 2>/dev/null || echo 0)"
  last_commit_subject="$(git log -1 --format=%s 2>/dev/null || echo '')"

  if [ "$last_commit_epoch" -eq 0 ]; then
    echo "watchdog: no git history; nothing to check"
    exit 0
  fi

  # If the last commit isn't a daily-run commit, we don't have a meaningful
  # baseline (operator may have been editing code). Skip.
  if [[ "$last_commit_subject" != newsletter:* && "$last_commit_subject" != newsletter\(* ]]; then
    echo "watchdog: HEAD is not a newsletter commit ($last_commit_subject) — skipping check"
    exit 0
  fi
fi

age_hours=$(( (now_epoch - last_commit_epoch) / 3600 ))

if [ "$age_hours" -lt "$STALE_HOURS" ]; then
  echo "watchdog: ok (last commit ${age_hours}h ago)"
  exit 0
fi

# Don't fire more than once every 4 hours, even if the condition persists.
if [ -f "$STATE_FILE" ]; then
  last_fire_epoch="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
  if [ $(( now_epoch - last_fire_epoch )) -lt $((4 * 3600)) ]; then
    echo "watchdog: stale (${age_hours}h) but throttled — last fired $(( (now_epoch - last_fire_epoch) / 60 ))m ago"
    exit 0
  fi
fi

echo "watchdog: STALE — last commit ${age_hours}h ago"
notify "Newsletter pipeline appears stuck" \
  "Last commit ${age_hours}h ago. Check logs/run-*.log."
echo "$now_epoch" > "$STATE_FILE"
