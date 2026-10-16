#!/usr/bin/env bash
# Register MCP servers as LaunchAgents (persistent, auto-restart on login).
#
# Default (safe): only bootstraps servers NOT already loaded in launchd.
#   scripts/mcp_admin/install_launchd.sh
#
# Force reinstall (bootout + bootstrap) for a specific server:
#   scripts/mcp_admin/install_launchd.sh --force jira-bob
#
# Force reinstall ALL (use when plist template changed):
#   scripts/mcp_admin/install_launchd.sh --force-all

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_servers.sh
source "$SCRIPT_DIR/_servers.sh"

TEMPLATE="$SCRIPT_DIR/launchd.plist.template"
LAUNCHAGENTS_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCHAGENTS_DIR" "$LOGDIR"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "ERROR: template not found: $TEMPLATE" >&2
  exit 1
fi

# Parse flags
FORCE_ALL=0
FORCE_NAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force-all) FORCE_ALL=1 ;;
    --force)     FORCE_NAME="${2:-}"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
  shift
done

loaded=0; skipped=0; failed=0

for entry in "${MCP_SERVERS[@]}"; do
  name="${entry%%|*}"
  rest="${entry#*|}"
  script="${rest%%|*}"
  port="${rest##*|}"

  plist_path="$LAUNCHAGENTS_DIR/bob.mcp.$name.plist"
  label="bob.mcp.$name"

  # Render plist from template (always — keeps file in sync).
  sed \
    -e "s|{{NAME}}|$name|g" \
    -e "s|{{PYTHON}}|$PYTHON|g" \
    -e "s|{{REPO_ROOT}}|$REPO_ROOT|g" \
    -e "s|{{SCRIPT}}|$script|g" \
    -e "s|{{PORT}}|$port|g" \
    -e "s|{{LOGDIR}}|$LOGDIR|g" \
    "$TEMPLATE" >"$plist_path"

  # Check if already running under launchd.
  already_loaded=0
  launchctl list "$label" >/dev/null 2>&1 && already_loaded=1

  force_this=0
  [[ $FORCE_ALL -eq 1 ]] && force_this=1
  [[ -n "$FORCE_NAME" && "$FORCE_NAME" == "$name" ]] && force_this=1

  if [[ $already_loaded -eq 1 && $force_this -eq 0 ]]; then
    echo "skip   $label (already running — use --force $name to reinstall)"
    skipped=$((skipped + 1))
    continue
  fi

  # Bootout first if loaded (force reinstall path).
  if [[ $already_loaded -eq 1 ]]; then
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
    sleep 1  # give launchd time to clean up before re-bootstrapping
  fi

  if launchctl bootstrap "gui/$UID" "$plist_path"; then
    echo "loaded $label  port=$port"
    loaded=$((loaded + 1))
  else
    echo "ERROR  failed to bootstrap $label — check plist: $plist_path" >&2
    failed=$((failed + 1))
  fi
done

echo ""
echo "Done: $loaded loaded, $skipped skipped, $failed failed."
if [[ $failed -gt 0 ]]; then
  echo "Run with --force <name> to retry a specific server." >&2
  exit 1
fi
echo "Verify: $SCRIPT_DIR/status.sh"
