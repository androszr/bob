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

## The panel

`./bob` starts a local web page (`http://localhost:3006`) that signs Bob in to Teams, starts, stops and
restarts him, and lets you manage chats, roles and approvals. It is an addition: the daemon command
above works exactly as before. Everything about it - tabs, `./bob install`, the guards - is on
[the panel page](panel.md).

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
`Bob pause 2h`, `Bob off`, `Bob on`. Pausing keeps the list of switched-off chats. A broadcast job of type
`weekly` (with `"weekday": 0` for Monday) runs once a week; a run started twice the same day posts only once.

## Roles

A chat can have one or more **roles** instead of a hand-written `mcp` list. A role is a named bundle of
tool groups (`dostep`), skills and knowledge (`wiedza`), kept in `roles.json` (copy `roles.example.json`;
the path can be moved with `BOB_ROLES_FILE`). In `chat-config.json`:

```json
"Project channel": { "role": ["project_manager"], "dodatkowo": ["todos"], "bez": ["jira_write"] }
```

- the chat's tools are the union of its roles' `dostep`, plus `dodatkowo`, minus `bez`;
- the role `asystent` is required and means **every default group** - exactly what `"mcp": null` meant, so a
  chat on `["asystent"]` gets the same prompt and tools as before;
- a chat **without** `role` keeps its own `mcp` / `mcp_extra`; an explicit `Bob chat NAME mcp ...` from the
  control chat puts a role chat back on a hand-written list;
- a group marked `"tylko_zespol": true` (team only) never reaches a chat flagged `"kanal_fr": true`, the flag
  for a channel with users outside your team, whatever its roles say;
- knowledge entries with `"wpiecie": "prompt"` are appended to the system prompt of the chats that have the
  role (a missing or too-long file is cut or skipped, never a failed reply); `"narzedzie"` and `"kod"` entries
  are described only;
- the file is read on every reply, so an edit applies from the next message. A broken `roles.json` is reported
  on stderr and the role chats lose their tools until it is fixed.

The panel's Roles tab edits this file, with a backup and a check before every save.

Before rolling back to an older build, put the chats' hand-written `mcp` lists back (without team-only groups on
`kanal_fr` chats) and remove `ingest_only`: older builds ignore `role`, `kanal_fr` and `ingest_only`, so a role chat
would get every default group and a listen-only chat would be answered.

If `roles.json` is missing or broken, any chat that has `role` or `kanal_fr` gets no tools at all (the failure is
printed on stderr); chats with neither keep working as before.

## Receive-only chats

`"ingest_only": true` on a chat in `chat-config.json` marks a channel Bob must never write to: he does not
answer there, never reacts and never posts a scheduled message. Every write path in `api_client.py` refuses the conversation, so even
a broadcast job pointed at it fails closed. Set `conv_id` for such a chat so the refusal also holds in separate
scripts.

## Reactions

When someone only acknowledges ("ok", "thanks, we'll talk Monday") Bob answers with the token
`[BOB-REAKCJA]` and the agent puts a thumbs-up on that message instead of writing a reply. The token can also
sit next to a short text.

## Long jobs

A reply may run for up to 25 minutes (tools, several lookups). After two minutes Bob posts one
"still working" note in the chat and edits it every three minutes; when the answer is ready the note says
how long it took.

## Approvals from the control chat

Yes-or-no requests opened in the panel (by you, Claude Code or a script) are announced once in your notes chat
(`48:notes`, the control chat of the example config) with an id. Answer there with "Bob, zatwierdzam apr-0123456789" or "Bob, odrzucam apr-0123456789: not now";
when exactly one request waits, "Bob, zatwierdzam" is enough. Only the owner can decide, and only from the
control chat.

## Self-healing sign-in

When the Teams token stops renewing, the daemon retries every minute. After two refreshes that return the same
dead token it opens Teams in a fresh browser window from `session.json` (what a manual restart does); after a
third it shows a macOS notification; when a new token arrives it says so in your notes chat. Before the
token's own expiry it also refreshes ahead of time instead of waiting for the first 401. On shutdown the
live session is saved, so a restart from the panel needs no new sign-in. If the session is gone and there is no
terminal, Bob exits with code 4 and asks you to sign in from the panel.

## Memory of the files Bob sent

Every text file Bob sends (`[WYSLIJ_PLIK]`) is also kept under `user-data/bob-files/` with a small registry.
When someone later links it, quotes Bob's message or mentions its name, the file's content is given to Bob as a
note, so "fix that file" works. A file is only read back in the chat it was sent to.

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
