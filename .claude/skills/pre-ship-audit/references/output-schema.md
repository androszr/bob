# Output Schema — Pre-Ship Audit

## Severity rubric

**BLOCKER**
Correctness, security, or convention violation that will cause data loss, secret
exposure, Teams bot crash, or silent wrong-behavior. Ship-blocking. Must be
resolved before commit/push.

**SHOULD-FIX**
Design issue, missing test for a new code branch, missed convention gap, or
maintainability problem that will compound. Not ship-blocking but must be
addressed in this PR or a tracked follow-up.

**NIT**
Naming preference, minor cleanup, stylistic suggestion. Mention once, do not belabor.

## Calibration rules

- If two agents disagree on severity for the same issue, take the higher and note why.
- A "missing test" is Should-fix only if the changed logic has a new branch. Pure
  refactors with no behavior change do not require new tests.
- A Nit already noted in a prior finding is suppressed (don't repeat).

## Per-finding format

```
[SEVERITY] <agent-name> — <file>:<line>
  <one-sentence description of the problem>
  Suggested: <concrete edit or action; omit if no clear fix exists>
```

Examples:

```
[BLOCKER] security-auditor — scripts/teams_agent/ai.py:31
  _ALLOWED_TOOLS includes "Bash" — allows Teams message content to trigger shell execution.
  Suggested: remove "Bash" from _ALLOWED_TOOLS; use MCP tools exclusively.

[SHOULD-FIX] repo-conventions-auditor — mcp-config.json:12
  New MCP server registered in mcp-config.json but not added to _ALLOWED_TOOLS in ai.py.
  Suggested: add mcp__<name>-bob__<tool> entries to _ALLOWED_TOOLS in ai.py.

[NIT] code-quality-auditor — scripts/teams_agent/cli.py:204
  Variable `d` could be named `msg_data` for clarity.
```

## Report structure

```
## Pre-Ship Audit Report
Scope: <pre-commit | pre-push | branch>  Changed files: N

### Blockers (N)
... findings ...

### Should-fix (N)
... findings ...

### Nits (N)
... findings ...

VERDICT: <OK TO SHIP | BLOCKERS — N issue(s) must be fixed | REVIEW — N should-fix issue(s), no blockers>
```

If there are zero findings in a band, omit that section header.
If there are zero findings total: `VERDICT: OK TO SHIP — no issues found`
