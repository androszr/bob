# Conventions — teams-agent

## Project structure

```
scripts/
  teams_agent/     # Core Teams agent (ai.py, cli.py, api_client.py, scraper.py, session.py)
  <name>/           # Each MCP server in its own folder
    mcp_server.py   # FastMCP server, transport stdio
  jira/             # Jira/Tempo MCP (special: has context.py, jira_client.py, skills.py)
knowledge/          # Domain knowledge files (Markdown, JSON)
  <topic>/          # Subfolder per knowledge area
tests/              # pytest, all files test_*.py
audits/             # /pre-ship-audit reports (gitignored, local only, never published)
.claude/
  agents/           # Subagent definitions (.md)
  skills/           # Skill definitions (SKILL.md per folder)
```

## Adding a new MCP server

Every new MCP server MUST follow this checklist. Missing items are SHOULD-FIX
(missing `mcp-config.json` registration is BLOCKER — bot won't have access to the tool).

1. **File location**: `scripts/<name>/mcp_server.py`
2. **FastMCP pattern**:
   ```python
   from mcp.server.fastmcp import FastMCP
   mcp = FastMCP("<name>-bob")
   @mcp.tool()
   def my_tool(...) -> str: ...
   if __name__ == "__main__":
       mcp.run(transport="stdio")
   ```
3. **Registration in `_MCP_SERVERS`** (`scripts/teams_agent/ai.py`) — a
   repo-relative path, nothing machine-specific:
   ```python
   "<name>-bob":  "scripts/<name>/mcp_server.py",
   ```
   Do NOT hand-edit `mcp-config.json`: it is gitignored and regenerated from
   `_MCP_SERVERS` on every `import ai`, using `sys.executable` and an absolute
   path derived from `_PROJECT_ROOT`.
4. **`--allowedTools` in `ai.py`**: add `mcp__<name>-bob__<tool_name>` entries to `_ALLOWED_TOOLS`.
   Omitting this means Claude won't call the tool even if it's registered.
5. **Test file**: `tests/test_<name>.py` — at minimum test authorization logic and happy path.

## Adding a new Claude invocation mode

New public functions in `ai.py` (e.g. a new reply mode) MUST:

1. Be a method of the `AI` class, not a standalone function
2. Call `_invoke_claude()` internally — never replicate subprocess logic
3. Define a timeout appropriate to the use case (never remove timeouts)
4. Be wired in `cli.py` under a named condition (chat config flag or message pattern)

## Timeouts — canonical values

| Mode | Timeout | Location |
|---|---|---|
| `freeform_reply` | 180s | `ai.py` |
| `grill_reply` | 90s | `ai.py` |
| `compact_history` | 20s | `ai.py` |

Changing these requires justification. Removing is BLOCKER.

## chat-config.json contract

Each chat entry may have:
- `conv_id` — Teams conversation ID (required)
- `name` — human label (required)
- `caller` — `all` | `owner` | `auto` (default: `all`)
- `knowledge` — list of knowledge folder names, or `null` for all
- `grill_active` — bool (default: false)

New fields added to chat-config.json must be handled in `cli.py` with a
safe default. Missing field should not crash the watcher.

## knowledge/ file format

- Markdown files: `<topic>/<name>.md` or `<topic>/<name>.knowledge.md` / `.context.md` / `.instructions.md`
- JSON data files: `<topic>/<name>.json`
- Each subfolder should have a `README.md` explaining what it contains
- Filenames: lowercase, hyphen-separated, ASCII-only

## Python style

- ruff enforced: E, F, W, I, B, UP, S rules; line-length 120
- No Polish diacritics in Python source strings (log messages, variable names, comments)
  — use ASCII transliterations or English. Polish is fine in knowledge/ Markdown files.
- `python-dotenv` for all secrets — never `os.environ` directly for sensitive values
- Atomic file writes: write to a temp file, then `os.replace()` to the target

## Git conventions

- Default branch for new features: `feature/<slug>`
- Direct commits to `main` only when user explicitly requests it
- Commit messages: Conventional Commits, ASCII-only, scopes from: `teams`, `mcp`, `jira`, `cli`, `ai`, `knowledge`, `chore`
- Never `--no-verify`
- Co-Authored-By trailer required on AI-assisted commits

## Encoding

- Python source files: ASCII-only in code (strings, comments, identifiers)
- JSON config files: UTF-8 allowed (Polish names in `chat-config.json` are fine)
- Knowledge Markdown: UTF-8, Polish content expected
