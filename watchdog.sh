#!/usr/bin/env bash
#
# Watchdog: fires a macOS notification if the newsletter pipeline appears stuck.
#
# Triggered hourly by ~/Library/LaunchAgents/com.kelly.agent-newsletter-watchdog.plist.
# Definition of "stuck": HEAD's commit timestamp is older than STALE_HOURS hours
# AND the most recent commit subject matches the daily-run pattern (so we don't
# false-fire when the user has been hand-editing code without committing).
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

STALE_HOURS="${STALE_HOURS:-36}"
STATE_FILE="$REPO_ROOT/logs/watchdog-last-fire"
mkdir -p "$REPO_ROOT/logs"

notify() {
  local title="$1"; shift
  local message="$1"; shift
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"${message//\"/\\\"}\" with title \"${title//\"/\\\"}\"" \
      >/dev/null 2>&1 || true
  fi
}

# Latest commit timestamp (epoch seconds).
last_commit_epoch="$(git log -1 --format=%ct 2>/dev/null || echo 0)"
last_commit_subject="$(git log -1 --format=%s 2>/dev/null || echo '')"
now_epoch="$(date -u +%s)"
age_hours=$(( (now_epoch - last_commit_epoch) / 3600 ))

if [ "$last_commit_epoch" -eq 0 ]; then
  echo "watchdog: no git history; nothing to check"
  exit 0
fi

# If the last commit isn't a daily-run commit, we don't have a meaningful
# baseline (operator may have been editing code). Skip.
if [[ "$last_commit_subject" != newsletter:* ]]; then
  echo "watchdog: HEAD is not a newsletter commit ($last_commit_subject) — skipping check"
  exit 0
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
