[Back to the README](../README.md)

# The panel

A small web page on your own computer that runs Bob without a terminal: it signs Bob in to Teams, starts, stops
and restarts him, shows what he did, and edits the chats and roles he works with. It is an addition - the
daemon command (`python3 scripts/teams_agent/cli.py`) still works exactly as before.

It is written for macOS, like the tool daemons: it finds Bob by his command line (`ps`), starts him in a login
`zsh` (so he gets your usual environment) and registers itself with `launchctl`. The Teams agent itself still
runs anywhere in its stdio mode.

## Start it

```bash
./bob            # start the panel if it is not running and open it in the browser
./bob status     # is the panel up, is Bob running, how old is the Teams session
./bob restart    # restart the panel (Bob keeps running)
./bob install    # start the panel at login and bring it back if it dies (a LaunchAgent, "bob.panel")
./bob logs       # follow the panel's own log
```

The page is at `http://localhost:3006`; set `BOB_PANEL_PORT` to use another port and `BOB_PANEL_SHELL` to
change the login shell Bob is started in (default `/bin/zsh`).

## The tabs

The tabs have Polish names on the page; the English names are in brackets.

| Tab | What it does |
|---|---|
| Pulpit (Dashboard) | One box says what to do now: sign in, start Bob, answer a waiting approval, or "all is well". Below: status cards, start / stop / restart, and a live 24-hour view of the chats (a heat map by hour, calls and cost per hour, the latest actions). It refreshes every 15 seconds. |
| Zgody (Approvals) | Yes-or-no requests from Bob, Claude Code or scripts, with a note field. Each new request is also announced in your notes chat, where you can answer it away from the computer. Recent decisions are listed under it. |
| Rozmowy (Conversations) | The latest messages Bob reacted to, who wrote, and what he answered or that he stayed silent; filter by chat. |
| Logi i problemy (Logs) | Bob's log, parsed into levels and sources. Problems of the last day come first, repeats are folded ("x3"), routine lines are hidden unless you ask. |
| Kanały (Chats) | Every chat in `chat-config.json`: roles, who may call Bob, tools, last activity. Add, change, switch off or remove a chat; every change is checked, shown as a diff first and backed up. |
| Role (Roles) | The roles in `roles.json`: tool groups, skills, knowledge and where each role works. Edit a role, describe a change to a knowledge file (Claude drafts it, the proposal goes to Approvals), switch tool-group descriptions. |
| Harmonogram (Schedule) | The scheduled posts with the time they last fired, and the background loops Bob runs on his own. |

If `roles.json` does not exist yet, the Roles tab says so; copy `roles.example.json` to `roles.json`.

## Sign in to Teams from the page

When the Teams session is missing or older than seven days, the dashboard offers "Zaloguj do Teams" (and "Zaloguj i
uruchom Boba"). It opens a normal browser window on this computer; sign in as usual (choose "stay signed in"),
and when you see the chat list press "Jestem zalogowany" on the panel. Bob will not start, and a restart is
refused, while the session is expired, so a restart can never leave Bob down. If Bob was running before a reboot
and the session is still valid, the panel starts him again by itself.

## Who can reach it

- It listens on `127.0.0.1` only.
- A request that changes anything (every POST) must carry an `Origin` of the panel's own address, and every
  request must carry a `Host` of `localhost` / `127.0.0.1` with the panel's port. A page on another site in your
  browser, or a name pointed at your computer, cannot drive Bob.
- Request bodies are limited to 64 KB, the static files are served only from the panel's own folder, and the
  page cannot be put in a frame.
- The panel starts Bob with a fixed command; nothing in a request body is ever run. Claude Code can use the same
  JSON API with `curl` and the `Origin` header.

**The panel trusts every process on this Mac.** Outside a browser the `Origin` and `Host` headers can be sent by
anything, so another local user account that can reach `127.0.0.1` on the panel's port can use it. It is meant for
a single-user Mac; there is no password or token layer.

Before rolling back to an older build, restore the chats' hand-written `mcp` lists (without team-only groups on
`kanal_fr` chats) and remove `ingest_only` (older builds ignore `role`, `kanal_fr` and `ingest_only`).

## One page, both screens

The page is a single tree of markup. A width breakpoint (900 px) stacks the navigation on top of the content on a
narrow window; nothing is duplicated for phones and the only thing hidden by width is the table of contents
inside the file reader.

## Look

The colours live in one file, `scripts/bob_panel/static/tokens.css` (light and dark; the page has a toggle). The font is the system font. The pictures are
the frog drawings in `static/bob/` (see its README) and a few hand-drawn icons in `static/icons/`.

## Files the panel keeps

Everything is under `user-data/bob-panel/` in the clone, which git ignores: Bob's output log (`bob.log`), the
supervisor's state (`supervisor.json`, including "Bob should be running"), `approvals.json`, backups of edited
`chat-config.json` and `roles.json`, and proposals for knowledge edits. Knowledge files the panel writes for a role
live in `knowledge/role/<role>/` and are git-ignored too. `scripts/teams_agent/bob-control.json` is
shared with the agent (pause, off, switched-off chats).

## Remove it

`launchctl bootout gui/$(id -u)/bob.panel`, delete `~/Library/LaunchAgents/bob.panel.plist`, and remove
`~/Library/Logs/bob/bob-panel.log`. The panel's state folder is inside the clone and goes with it.
