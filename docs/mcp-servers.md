[Back to the README](../README.md)

# MCP servers

The source of truth is `ai._MCP_SERVERS` / `ai._MCP_GROUPS` (`scripts/teams_agent/ai.py`)
and `scripts/mcp_admin/ports.py` (ports). The model only gets the tool groups listed for
that chat in `chat-config.json` (`mcp`, `mcp_extra`).

| Server | Port | Example tools |
|---|---|---|
| `jira-read-bob` / `jira-bob` | 7126 / 7101 | `find`, `jira_issue`, `jira_search`, `jira_create`, `jira_comment` |
| `todos-bob` | 7103 | `add_todo`, `list_todos`, `update_todo` |
| `md-to-mp3-bob` | 7110 | `convert_md_to_mp3`, `list_md_to_mp3_files` |
| `urlopy-bob` | 7112 | `urlopy_meta`, `urlopy_check`, `urlopy_list` |
| `datetime-bob` | 7113 | `now`, `resolve_date`, `weekday`, `date_range` |

Ports between 7101 and 7131 that are not in the table belonged to retired servers; `ports.py` keeps them reserved, so do not reuse them.

Tool groups per chat (`ai._MCP_GROUPS`): `jira`, `jira_write`, `jira_hypercare`, `todos`,
`datetime`, `urlopy`, `md_to_mp3`.

`mcp-config.json` is **generated** by `_refresh_mcp_config()` in `ai.py` (the transport
depends on `BOB_MCP_TRANSPORT`) - it is in `.gitignore`; do not edit it by hand.

## stdio mode (default)

`claude --print` starts one Python process per MCP server per message. No setup, but a few
seconds of overhead on every reply. Exception: the servers in `ai._DAEMON_SERVERS` (Jira
read, `jira-read-bob`) are started by `cli.py` itself as HTTP daemons - see
[`scripts/mcp_admin/README.md`](../scripts/mcp_admin/README.md#demon-jira-tryb-stdio).

## Persistent HTTP daemons (macOS, recommended)

The servers run as long-lived processes under launchd on `127.0.0.1` (ports from
`ports.py`), with a bearer token. Measured gain: about 36% on a typical Jira question.

```bash
scripts/mcp_admin/install_launchd.sh       # a LaunchAgent per server, autostart
scripts/mcp_admin/status.sh                # verify
export BOB_MCP_TRANSPORT=http              # e.g. in ~/.zshrc
python3 scripts/teams_agent/cli.py        # watch runs a healthcheck at start
```

Logs: `~/Library/Logs/bob/mcp-*.log`. After changing server code:
`launchctl kickstart -k "gui/$UID/bob.mcp.<name>"`. Rollback:
`unset BOB_MCP_TRANSPORT && scripts/mcp_admin/uninstall_launchd.sh`.

The launchd labels are `bob.mcp.<name>`, with plists under `~/Library/LaunchAgents/`.
More detail: [`scripts/mcp_admin/README.md`](../scripts/mcp_admin/README.md).
