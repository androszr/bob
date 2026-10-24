#!/usr/bin/env bash
# Register Bob's panel (http://localhost:3006) as a LaunchAgent: starts at login, restarted if it dies.
#
#   scripts/bob_panel/install_launchd.sh            # install / refresh
#   scripts/bob_panel/install_launchd.sh --force    # bootout + reinstall
#
# The panel supervises Bob (scripts/teams_agent/cli.py) - Bob itself is not a LaunchAgent: it keeps
# running across panel restarts and is restarted from the panel (or POST /api/bob/restart).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOGDIR="${BOB_MCP_LOGDIR:-$HOME/Library/Logs/bob}"
PYTHON="${BOB_MCP_PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"
TEMPLATE="$SCRIPT_DIR/launchd.plist.template"
LABEL="bob.panel"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

sed -e "s#{{PYTHON}}#$PYTHON#g" -e "s#{{REPO_ROOT}}#$REPO_ROOT#g" -e "s#{{LOGDIR}}#$LOGDIR#g" "$TEMPLATE" > "$PLIST_PATH.tmp"
if [[ -f "$PLIST_PATH" ]] && cmp -s "$PLIST_PATH" "$PLIST_PATH.tmp" && [[ $FORCE -eq 0 ]]; then
  rm "$PLIST_PATH.tmp"
else
  mv "$PLIST_PATH.tmp" "$PLIST_PATH"
  FORCE=1
fi

DOMAIN="gui/$(id -u)"
if [[ $FORCE -eq 1 ]]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
fi
launchctl bootstrap "$DOMAIN" "$PLIST_PATH" 2>/dev/null || true
launchctl kickstart "$DOMAIN/$LABEL" 2>/dev/null || true
echo "bob.panel -> http://localhost:3006 (log: $LOGDIR/bob-panel.log)"
