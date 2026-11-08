#!/usr/bin/env bash
# Shared server list for mcp_admin/*.sh scripts.
# Source this file; do not execute directly.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNDIR="${BOB_MCP_RUNDIR:-$HOME/.bob/mcp}"
LOGDIR="${BOB_MCP_LOGDIR:-$HOME/Library/Logs/bob}"
PYTHON="${BOB_MCP_PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"

# Single source of truth: name|relative/script.py|port
# Keep aligned with scripts/mcp_admin/ports.py
MCP_SERVERS=(
  "jira-bob|scripts/jira/mcp_server.py|7101"
  "todos-bob|scripts/todos/mcp_server.py|7103"
  "md-to-mp3-bob|scripts/md_to_mp3/mcp_server.py|7110"
  "leave-bob|scripts/leave/mcp_server.py|7112"
  "datetime-bob|scripts/datetime_bob/mcp_server.py|7113"
  "jira-read-bob|scripts/jira/mcp_read_server.py|7126"
  "snowflake-bob|scripts/snowflake_bob/mcp_server.py|7132"
)

# Names used by older builds. An upgrade can leave such a daemon running (holding its port) or its
# LaunchAgent installed (pointing at a script that no longer exists); retire_legacy_servers stops both.
LEGACY_MCP_SERVERS=(
  "urlopy-bob"
)

retire_legacy_servers() {
  local name pidfile pid label plist
  for name in "${LEGACY_MCP_SERVERS[@]}"; do
    pidfile="$RUNDIR/$name.pid"
    if [[ -f "$pidfile" ]]; then
      pid=$(cat "$pidfile")
      if kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5; do sleep 1; kill -0 "$pid" 2>/dev/null || break; done
        kill -KILL "$pid" 2>/dev/null || true
        echo "retire $name pid=$pid (renamed in this version)"
      fi
      rm -f "$pidfile"
    fi
    label="bob.mcp.$name"
    plist="$HOME/Library/LaunchAgents/$label.plist"
    if [[ -f "$plist" ]]; then
      launchctl bootout "gui/$UID/$label" 2>/dev/null || true
      rm -f "$plist"
      echo "retire $label LaunchAgent (renamed in this version)"
    fi
  done
}

mkdir -p "$RUNDIR" "$LOGDIR"
