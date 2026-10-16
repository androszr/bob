#!/usr/bin/env bash
# HTTP health probe for all 9 MCP servers. Prints OK/FAIL + response time.
# Exit code: 0 if all 9 OK, 1 otherwise.

set -uo pipefail  # NOT -e — we want to keep probing even if one fails.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_servers.sh
source "$SCRIPT_DIR/_servers.sh"

ok=0
fail=0
printf "%-18s %-6s %-9s %-10s %s\n" "NAME" "PORT" "STATUS" "TIME" "PID"
for entry in "${MCP_SERVERS[@]}"; do
  name="${entry%%|*}"
  rest="${entry#*|}"
  port="${rest##*|}"
  pidfile="$RUNDIR/$name.pid"
  pid=""
  [[ -f "$pidfile" ]] && pid=$(cat "$pidfile")

  # GET on streamable-http endpoint expects 401 (no bearer token) or 405/406 (only POST allowed). 200 if
  # framework changes. Anything that's a HTTP reply means the server is up.
  # Real failure: connection refused (curl exit 7) or timeout (28).
  resp=$(curl -s -o /dev/null -w "%{http_code}|%{time_total}" \
    --max-time 3 "http://127.0.0.1:$port/mcp" 2>/dev/null) || resp="000|0.000"
  code="${resp%%|*}"
  time_s="${resp##*|}"
  time_ms=$(awk -v t="$time_s" 'BEGIN{printf "%.0fms", t*1000}')
  if [[ "$code" != "000" ]]; then
    status="OK"
    ok=$((ok + 1))
  else
    status="FAIL"
    fail=$((fail + 1))
  fi
  printf "%-18s %-6s %-9s %-10s %s\n" "$name" "$port" "$status" "$time_ms" "${pid:--}"
done

echo ""
echo "Healthy: $ok / 9"
if (( fail > 0 )); then
  echo "Hint: scripts/mcp_admin/start_all.sh    (ad-hoc)"
  echo "      scripts/mcp_admin/install_launchd.sh  (persistent)"
  exit 1
fi
