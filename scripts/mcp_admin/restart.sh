#!/usr/bin/env bash
# Restart MCP servers. Usage:
#   restart.sh           — restart all (stop_all + start_all)
#   restart.sh <name>    — restart a single server (e.g. jira-bob)
# Notes:
#   - For launchd-managed daemons, use:
#       launchctl kickstart -k gui/$UID/bob.mcp.<name>
#     This script targets the start_all.sh / pidfile flow.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_servers.sh
source "$SCRIPT_DIR/_servers.sh"

target="${1:-__all__}"

if [[ "$target" == "__all__" ]]; then
  "$SCRIPT_DIR/stop_all.sh"
  "$SCRIPT_DIR/start_all.sh"
  exit $?
fi

# Single server flow.
for entry in "${MCP_SERVERS[@]}"; do
  name="${entry%%|*}"
  [[ "$name" == "$target" ]] || continue
  rest="${entry#*|}"
  script="${rest%%|*}"
  port="${rest##*|}"
  pidfile="$RUNDIR/$name.pid"
  logfile="$LOGDIR/mcp-$name.log"
  label="bob.mcp.$name"

  # launchd-managed? Kick that instance instead of touching the ad-hoc flow —
  # it already owns the port, so spawning a second process here would either
  # fail to bind or (worse) leave a stray untracked process behind.
  if launchctl print "gui/$UID/$label" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$UID/$label"
    echo "kickstart $label (launchd-managed, ad-hoc flow skipped)"
    exit 0
  fi

  if [[ -f "$pidfile" ]] && pid=$(cat "$pidfile") && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      sleep 1
      kill -0 "$pid" 2>/dev/null || break
    done
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$pidfile"
    echo "stop   $name pid=$pid"
  fi

  # Port already held by something not tracked in our pidfile (stale/foreign
  # process)? Refuse instead of starting a second server on top of it.
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ERROR  $name port $port already in use by another process (not launchd-managed, no pidfile match)" >&2
    exit 1
  fi

  cd "$REPO_ROOT"
  BOB_MCP_TRANSPORT=http nohup "$PYTHON" "$REPO_ROOT/$script" --http --port "$port" \
    >>"$logfile" 2>&1 &
  pid=$!
  echo "$pid" >"$pidfile"
  echo "start  $name pid=$pid port=$port"
  exit 0
done

echo "ERROR: unknown server '$target'. Known:" >&2
for entry in "${MCP_SERVERS[@]}"; do echo "  ${entry%%|*}" >&2; done
exit 1
