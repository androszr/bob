#!/usr/bin/env bash
# One-time setup for code-review-graph MCP integration.
# Idempotent — safe to re-run.
set -euo pipefail

PINNED_VERSION="2.3.2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

note() { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31mxx \033[0m %s\n" "$*" >&2; exit 1; }

# Check code-review-graph installed
if ! command -v code-review-graph >/dev/null 2>&1; then
  die "code-review-graph not found. Install: pipx install code-review-graph==$PINNED_VERSION"
fi

INSTALLED="$(code-review-graph --version 2>/dev/null | awk '{print $2}')"
note "code-review-graph $INSTALLED found"

# Build or update graph
cd "$REPO_ROOT"
if [ -f ".code-review-graph/graph.db" ]; then
  note "Updating existing graph"
  code-review-graph update
else
  note "Building initial graph (first run takes ~10s)"
  code-review-graph build
fi

cat << EOF

Done.
- Graph DB: $REPO_ROOT/.code-review-graph/graph.db (gitignored)
- MCP config: $REPO_ROOT/.mcp.json

Restart Claude Code in this directory to enable the code-review-graph MCP server.
Then use /review-pr to run a graph-aware review.
EOF
