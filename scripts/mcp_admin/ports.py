"""Stable HTTP ports for Bob's MCP servers.

Single source of truth for port assignment. Used by:
  - scripts/teams_agent/ai.py:_refresh_mcp_config (generates mcp-config.json URLs)
  - scripts/mcp_admin/launchd plist generator
  - scripts/mcp_admin/start_all.sh / status.sh / healthcheck.py
  - scripts/<dir>/mcp_server.py (default port when run with --http)

Range 7101-7199 is reserved for Bob. Ports missing from the table below
(7102-7125, 7127-7131) belonged to retired servers -- do not reuse them.
"""

MCP_PORTS: dict[str, int] = {
    "jira-bob":                7101,
    "todos-bob":               7103,
    "md-to-mp3-bob":           7110,
    "urlopy-bob":              7112,
    "datetime-bob":            7113,
    "jira-read-bob":           7126,
}


def http_url(name: str) -> str:
    """Return the streamable-http MCP endpoint URL for a server name."""
    return f"http://127.0.0.1:{MCP_PORTS[name]}/mcp"
