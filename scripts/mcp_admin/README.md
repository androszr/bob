# MCP admin

Lifecycle for Bob's MCP servers when running them as **persistent HTTP
daemons** instead of stdio subprocess-per-message.

## Why

Default (stdio) mode spawns one Python process per MCP server on every
`claude --print` invocation — seconds of cold-start overhead per watch tick.
HTTP mode keeps each server as a long-lived daemon listening on a stable
port, so claude CLI does only an HTTP handshake (~100ms total).

## Port map

Active servers:

| Server         | Port |
|----------------|------|
| jira-bob       | 7101 |
| todos-bob      | 7103 |
| md-to-mp3-bob  | 7110 |
| urlopy-bob     | 7112 |
| datetime-bob   | 7113 |
| jira-read-bob  | 7126 |

New servers get the next free port in `ports.py` — that file, not this table,
is authoritative. Ports 7102–7125 and 7127–7131 are reserved (retired servers);
never reuse them.

Source of truth: `scripts/mcp_admin/ports.py`. Mirrored in `_servers.sh`
for bash scripts.

## Enable HTTP mode

```bash
# 1. Install daemons under launchd (auto-restart, RunAtLoad).
scripts/mcp_admin/install_launchd.sh

# 2. Verify all of them are listening.
scripts/mcp_admin/status.sh

# 3. Tell ai.py to generate HTTP mcp-config.json by exporting the env var
#    in the shell that starts the watch loop:
export BOB_MCP_TRANSPORT=http
# (Add to ~/.zshrc or the watch launcher to persist across reboots.)

# 4. Start the watch as usual:
python3 -m teams_agent.cli watch ...
```

When `BOB_MCP_TRANSPORT` is unset (or != `http`), `ai.py` falls back to the
old stdio mode — launchd daemons are ignored, fully backwards compatible
(except the Jira read daemon, see below).

## Demon jira (tryb stdio)

W domyślnym trybie stdio jeden serwer i tak chodzi jako demon HTTP: serwer
odczytu Jiry (lista w `ai._DAEMON_SERVERS`). Nie wymaga launchd ani
`BOB_MCP_TRANSPORT`:

- **Start** — `cli watch` przy każdym starcie wywołuje
  `_ensure_daemon_servers(restart=True)`, które przez
  `scripts/mcp_admin/daemon.py` (`restart_daemon`) zatrzymuje poprzednią
  instancję i stawia nową, więc demon zawsze serwuje aktualny kod.
  Pidfile i log jak w skryptach shellowych: `~/.bob/mcp/<name>.pid`,
  `~/Library/Logs/bob/mcp-<name>.log` — `status.sh` / `stop_all.sh` widzą ten
  sam proces.
- **Watchdog** — pętla watch co `_DAEMON_WATCHDOG_INTERVAL_S` (60 s) sprawdza
  port i podnosi demona, jeśli przestał słuchać; potem regeneruje
  `mcp-config.json`.
- **Config** — `ai._refresh_mcp_config()` wpisuje dla tego serwera wpis HTTP
  tylko gdy port słucha **i** proces jest naszym demonem
  (`daemon.owned_and_listening`: pidfile + cmdline). W przeciwnym razie
  (demon padł, obcy proces na porcie) serwer wraca na stdio.
- **Obcy proces na porcie** — `restart_daemon` niczego nie zabija ani nie
  startuje, zwraca False; serwer zostaje na stdio. Sprawdź
  `lsof -nP -iTCP:<port> -sTCP:LISTEN`.
- Demon ma środowisko zamrożone przy starcie, więc nie widzi zmiennych
  per wiadomość (np. nadawcy) — dlatego jako demon chodzi tylko odczyt;
  zapisy do Jiry idą przez stdio.

## Rollback

```bash
unset BOB_MCP_TRANSPORT                          # ai.py back to stdio
scripts/mcp_admin/uninstall_launchd.sh           # daemons gone
```

## Day-to-day commands

| Command | Effect |
|---|---|
| `status.sh` | HTTP probe each port; OK/FAIL + response time. Exit 0 if all healthy. |
| `install_launchd.sh` | (Re)install all LaunchAgents and load them. Idempotent. |
| `uninstall_launchd.sh` | bootout + remove all plists. |
| `start_all.sh` | Ad-hoc (no launchd): nohup each server, write pid to `~/.bob/mcp/<name>.pid`. |
| `stop_all.sh` | Kill ad-hoc instances by pidfile. |
| `restart.sh` | Stop+start all ad-hoc instances. |
| `restart.sh <name>` | Stop+start one ad-hoc instance. |
| `healthcheck.py` | Block until all servers respond or timeout. Exit 0/1. |

## Logs

- Daemons under launchd: `~/Library/Logs/bob/mcp-<name>.log` (stderr) +
  `mcp-<name>.out` (stdout).
- Ad-hoc via `start_all.sh`: same directory.
- `BOB_MCP_LOGDIR=…` overrides the directory (env var, picked up by all scripts).

## After editing a server's code

LaunchAgents do NOT auto-reload after code changes.

```bash
# All servers:
scripts/mcp_admin/uninstall_launchd.sh && scripts/mcp_admin/install_launchd.sh

# Or one server (faster):
launchctl kickstart -k "gui/$UID/bob.mcp.jira-bob"
```

## Auth

Every HTTP request to a daemon must carry `Authorization: Bearer <token>`
(otherwise 401). `transport.run_mcp` wraps the app in `auth.BearerAuth`;
`ai._refresh_mcp_config` puts the header into every HTTP entry, and per-call
`--mcp-config` files are written 0600 under the rundir (never inline in argv).

- Token: `~/.bob/mcp/daemon-token` (0600; directory 0700), created on first use
  by whichever side (daemon or `ai.py`) comes first. `BOB_MCP_RUNDIR` overrides
  the directory.
- Rotation: delete `daemon-token` and every `mcp-config-*.json` in the rundir
  (they embed the old token), then restart the daemons and the watch loop.

## Troubleshooting

- **401 from a daemon** — the client has no/old token. Check the rundir is the
  same on both sides (`BOB_MCP_RUNDIR`), then rotate as above and restart
  daemons + watch loop. `status.sh` counts a 401 as "up".

- **`status.sh` shows FAIL for everything** — daemons not running. Run
  `install_launchd.sh` (or `start_all.sh` for ad-hoc).
- **port already in use** — `lsof -nP -iTCP:7101 -sTCP:LISTEN`. Something
  else is on a port from `ports.py`. Either kill it or change the port range in
  `ports.py` AND `_servers.sh` AND any installed plists (re-run
  `install_launchd.sh`).
- **`.env` not picked up** — daemons read `<repo>/.env` via a loader keyed
  off `Path(__file__).resolve()` (independent of cwd). Jira's client re-reads
  `ATLASSIAN_TOKEN` on every request; for other servers restart the daemon
  after rotating a key.
- **MCP server crashes in a loop** — check `mcp-<name>.log`. `ThrottleInterval`
  in the plist is 10s, so launchd will pause briefly between restarts.
