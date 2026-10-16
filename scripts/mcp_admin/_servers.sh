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
  "urlopy-bob|scripts/urlopy/mcp_server.py|7112"
  "datetime-bob|scripts/datetime_bob/mcp_server.py|7113"
  "jira-read-bob|scripts/jira/mcp_read_server.py|7126"
)

mkdir -p "$RUNDIR" "$LOGDIR"
