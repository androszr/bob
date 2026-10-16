#!/usr/bin/env bash
# Stop all MCP servers started by start_all.sh (matches on pidfile).
# Does NOT touch launchd-managed instances — use uninstall_launchd.sh for those.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_servers.sh
source "$SCRIPT_DIR/_servers.sh"

stopped=0
for entry in "${MCP_SERVERS[@]}"; do
  name="${entry%%|*}"
  pidfile="$RUNDIR/$name.pid"
  [[ -f "$pidfile" ]] || { echo "skip   $name (no pidfile)"; continue; }
  pid=$(cat "$pidfile")
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile"
    echo "stale  $name (pid=$pid not alive, cleared pidfile)"
    continue
  fi
  kill -TERM "$pid" 2>/dev/null || true
  # Wait up to 5s for graceful exit.
  for _ in 1 2 3 4 5; do
    sleep 1
    kill -0 "$pid" 2>/dev/null || break
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "force  $name pid=$pid (TERM ignored, sending KILL)"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
  echo "stop   $name pid=$pid"
  stopped=$((stopped + 1))
done

echo ""
echo "Stopped $stopped server(s)."
