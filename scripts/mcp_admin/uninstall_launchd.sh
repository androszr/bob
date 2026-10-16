#!/usr/bin/env bash
# Unload and remove LaunchAgent plists for all 9 MCP servers.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_servers.sh
source "$SCRIPT_DIR/_servers.sh"

LAUNCHAGENTS_DIR="$HOME/Library/LaunchAgents"

for entry in "${MCP_SERVERS[@]}"; do
  name="${entry%%|*}"
  label="bob.mcp.$name"
  plist_path="$LAUNCHAGENTS_DIR/$label.plist"

  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  if [[ -f "$plist_path" ]]; then
    rm -f "$plist_path"
    echo "removed $plist_path"
  else
    echo "skip    $plist_path (not present)"
  fi
done

echo ""
echo "All 9 MCP LaunchAgents uninstalled."
echo "If you want a fully clean state, also clear logs:"
echo "  rm -f $LOGDIR/mcp-*.log $LOGDIR/mcp-*.out"
