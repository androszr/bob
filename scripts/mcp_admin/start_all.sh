#!/usr/bin/env bash
# Start all 9 MCP servers as background processes on streamable-http transport.
# Use this in dev / when not running under launchd.
# For production (auto-restart, login) use scripts/mcp_admin/install_launchd.sh.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_servers.sh
source "$SCRIPT_DIR/_servers.sh"

started=0
for entry in "${MCP_SERVERS[@]}"; do
  name="${entry%%|*}"
  rest="${entry#*|}"
  script="${rest%%|*}"
  port="${rest##*|}"
  pidfile="$RUNDIR/$name.pid"
  logfile="$LOGDIR/mcp-$name.log"

  # Already running?
  if [[ -f "$pidfile" ]] && pid=$(cat "$pidfile") && kill -0 "$pid" 2>/dev/null; then
    echo "skip   $name (pid=$pid already running)"
    continue
  fi

  # Port in use by something else?
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ERROR  $name port $port already in use by another process" >&2
    exit 1
  fi

  cd "$REPO_ROOT"
  BOB_MCP_TRANSPORT=http nohup "$PYTHON" "$REPO_ROOT/$script" --http --port "$port" \
    >>"$logfile" 2>&1 &
  pid=$!
  echo "$pid" >"$pidfile"
  echo "start  $name pid=$pid port=$port log=$logfile"
  started=$((started + 1))
done

echo ""
echo "Started $started new server(s)."
echo "Status: $SCRIPT_DIR/status.sh"
