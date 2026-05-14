#!/usr/bin/env bash
#
# Install the launchd agents for the AI-Agents newsletter pipeline.
#
# Idempotent: re-running just reloads. To stop:
#   ./launchd/install.sh --uninstall
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$TARGET_DIR"

PLISTS=(
  "com.kelly.agent-newsletter.plist"
  "com.kelly.agent-newsletter-watchdog.plist"
)

uninstall() {
  for p in "${PLISTS[@]}"; do
    local target="$TARGET_DIR/$p"
    local label="${p%.plist}"
    if [ -f "$target" ]; then
      launchctl unload "$target" 2>/dev/null || true
      rm -f "$target"
      echo "removed $target"
    fi
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  done
  echo "done. all agents uninstalled."
  exit 0
}

if [ "${1:-}" = "--uninstall" ]; then
  uninstall
fi

for p in "${PLISTS[@]}"; do
  src="$SCRIPT_DIR/$p"
  target="$TARGET_DIR/$p"
  if [ ! -f "$src" ]; then
    echo "skip: $src not found"
    continue
  fi
  cp "$src" "$target"
  # bootstrap reloads if already loaded; suppress the harmless "already loaded" error.
  launchctl bootout "gui/$(id -u)/${p%.plist}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$target"
  echo "installed $target"
done

echo
echo "Status:"
for p in "${PLISTS[@]}"; do
  label="${p%.plist}"
  state="$(launchctl print "gui/$(id -u)/$label" 2>/dev/null | awk -F' = ' '/state =/ {print $2; exit}' || true)"
  printf '  %-50s %s\n' "$label" "${state:-not loaded}"
done

echo
echo "To trigger a run manually (without waiting for the schedule):"
echo "  launchctl kickstart -k gui/$(id -u)/com.kelly.agent-newsletter"
