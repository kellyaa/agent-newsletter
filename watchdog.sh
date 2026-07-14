#!/usr/bin/env bash
#
# Watchdog: fires a macOS notification if the newsletter pipeline appears stuck.
#
# Triggered hourly by ~/Library/LaunchAgents/com.kelly.agent-newsletter-watchdog.plist.
# Definition of "stuck": the most recent `newsletter:` commit on the CONTENT
# branch (where run.sh commits its daily artifacts) is older than STALE_HOURS.
#
# The main branch is checked as a fallback so historical `newsletter:` commits
# there (from before the content-branch split) still work; if neither branch
# has a recent `newsletter:` commit, we fall back to the previous "skip when
# HEAD is a human commit" behavior so users hand-editing code do not get
# spurious notifications.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

STALE_HOURS="${STALE_HOURS:-36}"
STATE_FILE="$REPO_ROOT/logs/watchdog-last-fire"
CONTENT_WORKTREE="${CONTENT_WORKTREE:-$REPO_ROOT/.worktrees/content}"
mkdir -p "$REPO_ROOT/logs"

notify() {
  local title="$1"; shift
  local message="$1"; shift
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"${message//\"/\\\"}\" with title \"${title//\"/\\\"}\"" \
      >/dev/null 2>&1 || true
  fi
}

# Find the most recent `newsletter:` commit across the content-branch
# worktree and main-branch history. run.sh commits into $CONTENT_WORKTREE on
# the `content` branch — those commits never appear in main's `git log`.
newest_newsletter_epoch=0
newest_newsletter_source=""

if [ -d "$CONTENT_WORKTREE/.git" ] || [ -f "$CONTENT_WORKTREE/.git" ]; then
  ct="$(git -C "$CONTENT_WORKTREE" log -1 --format=%ct --grep='^newsletter:' 2>/dev/null || echo 0)"
  if [ "${ct:-0}" -gt "$newest_newsletter_epoch" ]; then
    newest_newsletter_epoch="$ct"
    newest_newsletter_source="content-worktree"
  fi
fi

# Also scan any local `content` branch (if the operator uses a checkout
# rather than a worktree) and all reachable refs for a newsletter: commit.
# --all is deliberate: covers both the fresh-clone case and setups where
# content branch is only present as a remote-tracking ref.
ct="$(git log -1 --all --format=%ct --grep='^newsletter:' 2>/dev/null || echo 0)"
if [ "${ct:-0}" -gt "$newest_newsletter_epoch" ]; then
  newest_newsletter_epoch="$ct"
  newest_newsletter_source="git-history"
fi

# Latest HEAD commit info (used for the human-edit skip fallback).
last_commit_epoch="$(git log -1 --format=%ct 2>/dev/null || echo 0)"
last_commit_subject="$(git log -1 --format=%s 2>/dev/null || echo '')"
now_epoch="$(date -u +%s)"

if [ "$last_commit_epoch" -eq 0 ] && [ "$newest_newsletter_epoch" -eq 0 ]; then
  echo "watchdog: no git history; nothing to check"
  exit 0
fi

# Prefer the newest newsletter: commit (from content or main). If neither
# branch has ever seen one, fall back to the old skip-when-human-editing
# behavior so operators editing code don't get spurious warnings.
if [ "$newest_newsletter_epoch" -gt 0 ]; then
  last_commit_epoch="$newest_newsletter_epoch"
  age_hours=$(( (now_epoch - last_commit_epoch) / 3600 ))
  echo "watchdog: using newest newsletter: commit from $newest_newsletter_source (${age_hours}h ago)"
else
  age_hours=$(( (now_epoch - last_commit_epoch) / 3600 ))
  if [[ "$last_commit_subject" != newsletter:* ]]; then
    echo "watchdog: no newsletter: commit found on content or main; HEAD is '$last_commit_subject' — skipping check"
    exit 0
  fi
fi

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
