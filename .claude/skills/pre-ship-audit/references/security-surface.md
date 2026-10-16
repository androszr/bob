# Security Surface — teams-agent

## Files that must NEVER be committed (BLOCKER if found in diff)

| File / Pattern | Why |
|---|---|
| `.env` | ATLASSIAN_TOKEN, Tempo credentials, any API keys |
| `scripts/teams_agent/session.json` | Microsoft Teams SSO cookies + Bearer token |
| `scripts/teams_agent/user-data/` | Personal Teams message content |
| `scripts/teams_agent/log.jsonl` | Audit log with raw message content |
| `scripts/teams_agent/watch-state*.json` | Operational state (gitignored, regenerable) |
| `scripts/teams_agent/*-sniff.jsonl` | Network debug captures with Teams traffic |

## Files that must stay tracked (removing = BLOCKER)

None currently. `scripts/todos/todos.json` is gitignored (live task data) and must stay untracked.

## Attack surface — review focus

### 1. Teams message → claude CLI (highest-risk flow)

**Location:** `scripts/teams_agent/ai.py` — `_invoke_claude()`

The Teams message content flows as `stdin` to `subprocess.run(cmd, input=user_message, ...)`.
This is safe by design — stdin is not a shell injection sink. The risk is in `cmd` construction:

- BLOCKER: `--allowedTools` must NEVER include `Bash`, `Read`, `Write`, `Edit`, `WebFetch`, `WebSearch`.
  An attacker controlling a Teams message could instruct Claude to read files, exfiltrate secrets,
  or execute shell commands if these tools are enabled.
- Any change that adds tools to `_ALLOWED_TOOLS` in `ai.py` requires explicit justification.
- BLOCKER: `--mcp-config` path must be a hardcoded project path, never derived from message content.

### 2. MRI-based authorization in MCP tools

**Location:** `scripts/todos/mcp_server.py` and future MCP servers

`BOB_SENDER_MRI` is set from Teams API response in `cli.py` — NOT from message content.
This is the critical invariant. If any MCP server reads authorization from message content
instead of `os.environ["BOB_SENDER_MRI"]`, it is a BLOCKER.

Pattern to verify in any new MCP server with write operations:
```python
sender_mri = os.environ.get("BOB_SENDER_MRI", "")
if sender_mri != _OWNER_MRI and sender_mri != creator_mri:
    raise PermissionError(...)
```

### 3. claude CLI timeout values

**Location:** `ai.py` — `freeform_reply()`, `grill_reply()`, `compact_history()`

Established timeouts: freeform=180s, grill=90s, compact=20s.
Increasing these without justification could enable DoS via slow Teams messages.
Removing timeouts entirely is a BLOCKER.

### 4. session.json permissions

**Location:** `scripts/teams_agent/login.py`

After saving `session.json`, `os.chmod(SESSION_FILE, 0o600)` must be called.
Any change that removes this chmod or changes the mode to world-readable is a BLOCKER.

### 5. Secrets via environment

All secrets must come from `.env` via `python-dotenv` / `os.environ`.
Never hardcode tokens, passwords, or conversation IDs in source files.
`chat-config.json` is an exception — it holds non-secret conv_ids.

### 6. Prototype-equivalent: dict key injection in MCP tools

MCP tool arguments come from Claude (which is processing untrusted Teams content).
Any MCP tool that uses argument values as file paths, subprocess args, or dict keys
must validate them. Path traversal (`../../`) in a knowledge file path is a BLOCKER.
