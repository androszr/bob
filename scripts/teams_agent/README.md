# Bob — an AI assistant for Jan on Teams

Bob is an AI agent that runs in the background and answers Teams messages on behalf of Jan Kowalski. It reads and sends messages through the Teams REST API (not through the DOM) and generates replies through the Claude CLI.

**Persona:** Bob, the team's made-up assistant mascot. He knows product discovery, Scrum and OKRs — not programming.

## First run

```
python scripts/teams_agent/login.py
```

It opens Chromium in a visible window. Sign in to Teams with Microsoft SSO (MFA if required).
**Choose "Stay signed in" = Yes** — without it Microsoft does not save a lasting cookie.
Wait until you see Teams (the chat list on the left), go back to the terminal and press Enter.

The session is saved to `session.json` (mode 600, gitignored). It is valid for ~7 days.

## Normal run

```
python scripts/teams_agent/cli.py
```

Bob starts in daemon mode on the chats in `chat-config.json` (or **Notes** when there is none):
- Reads new messages through the REST API (no DOM scraping)
- Answers every message addressed to him as Bob
- A cursor based on `sequence_id` — restarts cause no repeats
- His own messages are ignored (echo prevention through `sender_mri`)

Ctrl-C to stop.

## Asking Bob directly

Jan can write to Bob straight from Teams:

```
Bob, what do you think of this roadmap?
Bob: when is our retrospective?
```

Bob answers only messages addressed to him — the owner's other messages are ignored.

## Options

```
--model MODEL        Claude model (default: claude-sonnet-5-5)
--force-login        Force a new SSO sign-in
--log-file PATH      Path to the JSONL log (default: log.jsonl)
--headless           Chromium without a window. Not recommended — Teams rejects headless sessions.
--watch NAME         Names of the chats to watch (default: every chat in chat-config.json)
--poll N             Polling interval in seconds (default: 5, min 1)
--rate-limit N       Minimum time between replies in seconds (default: 3)
--watch-state PATH   State file (default: watch-state.json)
--interactive        Manual mode with an approval menu instead of the daemon
--sniff-api          Log Teams HTTP requests to api-sniff.jsonl (diagnostics)
--sniff-ws           Passive mode: log WebSocket frames to ws-sniff.jsonl, sends nothing
```

## Passive mode (WebSocket sniff)

```
python scripts/teams_agent/cli.py --sniff-ws
```

Bob only listens — he answers nothing. Every Teams WebSocket frame goes to `ws-sniff.jsonl`. Useful for learning the format of real-time events before building a push mode (no polling).

## Interactive mode

```
python scripts/teams_agent/cli.py --interactive
```

The classic mode with an approval menu: `[s]end / [e]dit / [r]egen / [n]ext / [q]uit`

## Architecture

| Component | Role |
|-----------|------|
| `login.py` | One-time SSO sign-in through Playwright, saves `session.json` |
| `api_client.py` | Teams REST API — reads and sends messages with the cookies from `session.json` |
| `ai.py` | Calls the Claude CLI (`--tools` + `--allowedTools` + `--permission-mode dontAsk`) — no Bash, `Read` only for images (a folder per call) |
| `scraper.py` | Playwright DOM — used only for navigation (session keepalive) |
| `cli.py` | Orchestrator: watch loop, sniffers, interactive mode |
| `session.py` | Keeps track of how fresh `session.json` is |
| `roles.py` | Chat roles (`roles.json`): access to tool groups, skills, knowledge appended to the prompt; a pure module, the same for Bob and the panel |
| `token_health.py` | State machine for renewing the Teams token: when to reopen the browser, when to raise a desktop alert |
| `bob_files.py` | Memory of the files Bob sent (`user-data/bob-files/`): when someone points at a file, Bob gets its content |

## Files (gitignored)

- `session.json` — Playwright cookies (never commit)
- `log.jsonl` — audit log of sent messages
- `watch-state.json` — the last `sequence_id` per chat (lets Bob resume after a restart)
- `timers-state.json` — when the slow recurring jobs last ran (SharePoint warm-up, leave sheet polling); a restart does not repeat them
- `perf-metrics.jsonl` — time and cost of every Claude call (the source for stats and the panel)
- `bob-control.json` — Bob's pause / off state and the list of switched-off chats (written atomically, the panel reads it too)
- `api-sniff.jsonl` — HTTP logs from `--sniff-api`
- `ws-sniff.jsonl` — WebSocket logs from `--sniff-ws`

## Security

The Claude CLI is called with `--tools` (defines the built-in tools: none, or only WebSearch/WebFetch/Read), `--allowedTools` (the explicit MCP tools) and always with `--permission-mode dontAsk` (anything not on the list is refused). The model cannot run shell commands or write anything. `Read` is present only in calls with images and works only inside the `bob_read_*` folder created for that one call (copies of the images, also the cwd). WebFetch is off when MCP tools with company data are loaded (WebSearch stays). The HTTP MCP daemons require a Bearer token, and `cli.main()` sets `umask 077` (secrets and chat data are `0600`). This holds whatever the incoming message says.
