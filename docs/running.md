[Back to the README](../README.md)

# Running Bob

## Daemon (default)

```bash
python3 scripts/teams_agent/cli.py [--watch NAME ...] [--poll N] [--rate-limit N]
                                    [--model MODEL] [--caller owner|all|auto]
```

| Flag | Default | Meaning |
|---|---|---|
| `--watch` | the chats from `chat-config.json` | One or more channels to watch (matched on a fragment of the name) |
| `--poll` | `5` | REST polling interval (s) |
| `--rate-limit` | `3` | Minimum gap between replies (s) |
| `--model` | `claude-sonnet-5-5` | An alias (`haiku`/`sonnet`/`opus`) or a full name |
| `--caller` | `all` | Who triggers a reply: `owner` (owner only), `all` (anyone, mentioning "Bob"), `auto` (every message) |
| `--watch-state` | `watch-state.json` | The `sequence_id` cursor per channel |
| `--log-file` | `log.jsonl` | JSONL audit (every send and skip) |

The `sequence_id` cursor means a restart does not reply twice.

## Interactive - manual approval

```bash
python3 scripts/teams_agent/cli.py --interactive
```

A draft for every unread chat, with the menu `[s]end / [e]dit / [r]egen / [n]ext / [q]uit`.

## Sniff

```bash
python3 scripts/teams_agent/cli.py --sniff-api   # HTTP -> api-sniff.jsonl
python3 scripts/teams_agent/cli.py --sniff-ws    # WebSocket -> ws-sniff.jsonl (passive)
```

For inspecting endpoints and events without sending anything.

## Control commands

The owner can pause, disable and resume Bob from the control chat: `Bob pause 30m`,
`Bob pause 2h`, `Bob off`, `Bob on`.

## Channel personas

On a chosen channel another persona can answer instead of Bob: a different base prompt,
label and emoji, and its own trigger word. An entry in `chat-config.json`:

```json
"Product channel": { "persona": "example", "trigger": ["assistant"], "caller": "auto" }
```

The repository holds one template persona:
[`scripts/teams_agent/prompts/example_persona.md`](../scripts/teams_agent/prompts/example_persona.md)
(its own label and a light-bulb emoji). It shows the two tokens handled in `cli.py`:
`[EXAMPLE-POMYSL: title | body]` (an idea saved to `user-data/example/pomysly.jsonl`) and
`[EXAMPLE-UCZY-SIE: title | body]` (learning - saved **only** when the owner sent the
message, checked by MRI). A new persona is a prompt in `prompts/`, an entry in
`ai._PERSONA_PROMPTS` and a label in `api_client.AGENT_LABELS` / `_AGENT_EMOJI`.

The Bob mascot GIFs (`[BOB_GIF: mood]`) are **not included in the repository**. Set
`BOB_GIF_DIR` to your own set of files (names in `scripts/teams_agent/bob_gifs.py`);
without them no GIF is sent.

## Per-chat knowledge

`chat-config.json` maps a chat to a filter over the files in `knowledge/`:

```json
{ "chats": { "AI channel": { "knowledge": null } } }
```

- `null` (or absent) - all files from `knowledge/`
- `[]` - no files
- `["foo.md", "bar.md"]` - only the ones listed

## The sandbox around the model

The `claude` CLI starts with `--tools` (built-in tools: empty, or WebSearch/WebFetch/Read),
`--allowedTools` (the explicit MCP tools) and `--permission-mode dontAsk`. The model has no
shell and no built-in file tools (Write/Edit); the only writes it can cause are the ones its MCP tools make (Jira changes, `todos.json`, MP3s under `MD_TO_MP3_DIR`, default `user-data/md-to-mp3/`); `Read` is for images only and only in the call's own
`bob_read_*` directory; WebFetch is switched off whenever tools that carry team data are
loaded. The HTTP MCP daemons require a bearer token, and the Teams daemon runs under
`umask 077` with tokens and the session written `0600`. "Owner only" actions (for example persona learning) are checked
in code against the sender's MRI, not only in the prompt.

## Broadcasts and the recap script

`scripts/broadcast/broadcast_runner.py` posts scheduled, AI-generated messages (a standup,
planning prep) from the briefs under `scripts/broadcast/briefs/`, driven by `jobs.json` and
`broadcast-schedule.json`. Among the example jobs
are a daily standup, planning prep, a daily worklog note and a missing-worklog reminder for
managers (`worklog-missing-recap`, no AI).

`scripts/chat_recap.py` is a separate stand-alone script, not a broadcast: it summarises
local Claude Code chat files (`source_data/chats/*.jsonl`) for a day into
`knowledge/daily-recap/YYYY-MM-DD.md` and prints the result. It never posts to Teams.
