[Back to the README](../README.md)

# Setup

## Install

```bash
pip install -r requirements.txt -c constraints.txt        # runtime
pip install -r requirements-dev.txt -c constraints.txt    # pytest + ruff
playwright install chromium
brew install ffmpeg   # only for md-to-mp3 (Gemini TTS -> MP3)
```

Python 3.11+ is required. The `claude` CLI (Claude Code) must be on your `PATH`; the
default model is `claude-sonnet-5-5`.

## Configuration files

Every file with real values is in `.gitignore`; the repository holds only examples with
made-up data. Copy them and fill them in:

```bash
cp .env.example .env
cp chat-config.example.json chat-config.json
cp scripts/teams_agent/people.example.json scripts/teams_agent/people.json
cp config/jira-fields.example.json config/jira-fields.json
cp scripts/broadcast/jobs.example.json scripts/broadcast/jobs.json
cp scripts/broadcast/worklog-teams.example.json scripts/broadcast/worklog-teams.json
cp scripts/broadcast/broadcast-schedule.example.json scripts/broadcast/broadcast-schedule.json
```

| File | What it sets |
|---|---|
| `.env` | the owner (`OWNER_TEAMS_USER_ID`, `OWNER_DISPLAY_NAME`, `OWNER_ALIAS`, `OWNER_JIRA_LOGIN`, `ORG_EMAIL_DOMAIN`), Jira (`ATLASSIAN_TOKEN`, `JIRA_URL`, `JIRA_BOARD_ID`, `JIRA_PROJECT`, `JIRA_TEAMS`), SharePoint (`SHAREPOINT_TENANT`, `URLOPY_SHAREPOINT_URL`, `SHAREPOINT_UPLOAD_FOLDER_URL`), optionally `GIPHY_API_KEY`, `BOB_MCP_TRANSPORT` |
| `chat-config.json` | which chats to watch and how: MCP groups, internet access, trigger, persona, model, `hold_s` |
| `people.json` | people to @-mention with the `[BOB-WYWOLAJ: key]` token (path: `PEOPLE_FILE`) |
| `config/jira-fields.json` | the fields and issue types of your Jira (path: `JIRA_FIELDS_FILE`) |

### Every `.env` variable, grouped as in `.env.example`

- **Jira / Atlassian:** `ATLASSIAN_TOKEN`, `JIRA_URL`, `JIRA_BOARD_ID`, `JIRA_PROJECT`,
  `JIRA_TEAMS`, `OWNER_JIRA_LOGIN`, `ORG_EMAIL_DOMAIN`, `JIRA_FIELDS_FILE`, `VERIFY_SSL`
- **md-to-mp3 (Gemini TTS):** `GOOGLE_GEMINI`, `GEMINI_BASE_URL`, `MD_TO_MP3_DIR`,
  `MD_TO_MP3_TTS_CONCURRENCY`, `MD_TO_MP3_VOICE_KOBIETA_ID`, `MD_TO_MP3_VOICE_MEZCZYZNA_ID`
- **Owner and organisation:** `OWNER_TEAMS_USER_ID`, `OWNER_DISPLAY_NAME`, `OWNER_ALIAS`,
  `SHAREPOINT_TENANT`, `URLOPY_SHAREPOINT_URL`, `SHAREPOINT_UPLOAD_FOLDER_URL`,
  `PEOPLE_FILE`, `NAME_NOISE_PREFIXES`
- **The Teams daemon:** `GIPHY_API_KEY`, `BOB_MCP_TRANSPORT`, `TEAMS_ROBOT_DEBUG`

A variable exported in the shell wins over `.env`. An empty value means "use the default
from the code".

### Where the values are read

Values are read in `scripts/org_config.py`. A missing value is never guessed: the
function that needs it reports "not configured", and the "owner only" gates stay closed
until you set `OWNER_TEAMS_USER_ID`.

Build broadcast briefs (`scripts/broadcast/briefs/*.md`) from `daily-standup.example.md`
and `planning-prep.example.md`, and point to them from `jobs.json` with `brief_file`.

## Quick start in two commands

```bash
# 1. One-time SSO - opens a visible Chromium. Sign in, tick
#    "Stay signed in = Yes", wait for the chat list, come back and press Enter.
python3 scripts/teams_agent/login.py

# 2. The daemon - watches the chats from chat-config.json (no file = only the default notes chat).
python3 scripts/teams_agent/cli.py
```

Sign-in notes: `login.py` opens a visible Chromium for SSO and saves `session.json`.
Bob never asks for your password; it reuses the **bearer token from your own browser
session**, which is why the browser must be the visible one (Teams refuses headless
Chromium, even with valid cookies). The session lives about seven days; then run
`login.py` again.

Bob answers messages containing the word "Bob" (or the channel trigger from
`chat-config.json`). Its own messages are recognised by `sender_mri` and the label
prefix, so there is no echo. Ctrl-C stops it.

## Persistent state (everything is in `.gitignore`)

| File | Purpose |
|---|---|
| `scripts/teams_agent/session.json` | Playwright storage_state (cookies, mode 600) |
| `scripts/teams_agent/.auth-token`, `.ic3-auth-token` | Teams bearer tokens (chatsvc, AMS) - they work like a password and stay local |
| `scripts/teams_agent/watch-state.json` | cursor `{chat: {last_sequence_id}}` |
| `scripts/teams_agent/log.jsonl` | audit of every daemon action |
| `scripts/teams_agent/api-sniff.jsonl`, `ws-sniff.jsonl` | dumps of the sniff modes |
| `user-data/` | history, profiles of the people who talk to Bob, persona ideas and learning |
| `.env` | tokens and settings - **never** commit |

The Teams daemon runs under `umask 077`, and the session and token files are written `0600` whatever the umask; the launchd tool daemons and `chat_recap.py` / `broadcast_runner.py` use your normal umask.

## Tests and lint

```bash
pytest tests/ -q
ruff check .
```

The tests mock Playwright, the REST API and the `claude` CLI - they do not need a
running Teams.

## Known limitations

- Headless Chromium does not work - Teams forces sign-in in a visible browser.
- The session lasts about seven days; after that, run `login.py` again.
- The internal Teams APIs are undocumented and may change (`--sniff-api` is for diagnosis).
- The `claude` CLI is synchronous (a few seconds per reply) - the main bottleneck.
- The persistent HTTP daemon mode assumes macOS (launchd); stdio mode works anywhere.
