---
name: review-pr
description: Review a branch diff using the code-review-graph MCP server for
  blast-radius analysis and structural context. Use when the user says "review this
  branch", "review my changes against main", "zrob review", "sprawdz branch",
  or invokes /review-pr. Graph-aware branch-level review — complements /pre-ship-audit
  (which runs linters and four specialist auditors on a diff).
argument-hint: "[branch name]"
---

# Review PR (graph-aware)

Performs a structural code review using the `code-review-graph` MCP server.

**Prerequisite:** the MCP server must be running. If graph tools are not available,
stop and tell the user to run `scripts/bootstrap-code-graph.sh` and restart Claude Code.

**Token discipline:** call `get_minimal_context_tool` first; only escalate to fuller
context for high-risk files.

## Steps

1. **Identify the diff scope:**
   - Branch name in args → `git diff main...<branch>`
   - Otherwise → auto-detect current branch vs `main`
   - Empty diff → stop, tell the user.

2. **Refresh the graph:** call `build_or_update_graph_tool(base="main")`.
   Incremental update; should complete in <5s.

3. **Fetch review context:** call `get_review_context_tool(base="main")`.
   Returns changed-file set + structural summary (functions touched, callers/callees at depth 1).

4. **Compute blast radius:** call `get_impact_radius_tool(base="main")`.
   Note files/functions with largest downstream reach — high-risk surfaces.

5. **Per-file deep dive (high-risk first):**
   - For risky functions: `query_graph_tool(pattern="callers_of", target=<func>)` or
     fall back to `semantic_search_nodes_tool(query="callers of <func>")`.
   - For test coverage: `query_graph_tool(pattern="tests_for", target=<func>)` or
     fall back to `Bash("grep -r '<func>' tests/")`.
     Missing tests on changed risky functions is a finding.
   - Only `Read` actual file source when graph data is insufficient.

6. **teams-agent specific checks:**
   - **allowedTools gate**: if diff touches `ai.py`, verify `_ALLOWED_TOOLS` does not include
     `Bash`, `Read`, `Write`, `Edit`, `WebFetch`. Flag as BLOCKER if added.
   - **MRI authorization**: if diff adds a new MCP tool with write operations, verify
     `BOB_SENDER_MRI` check from `os.environ` is present.
   - **Timeout contract**: if diff touches `_invoke_claude()` or adds a new public AI method,
     verify timeout is set.
   - **MCP registration**: if diff adds `scripts/<name>/mcp_server.py`, verify it's registered
     in `mcp-config.json` and `_ALLOWED_TOOLS`.

7. **Output (structured):**

   ```
   ## Branch Review: <branch>

   ### Summary
   <1-3 sentences>

   ### Risk assessment
   - Overall risk: Low / Medium / High
   - Blast radius: X files, Y functions impacted
   - Test coverage: N changed functions covered / M total

   ### File-by-file
   #### <path>
   - Changes: <what>
   - Impact: <who depends on it>
   - Issues: <bugs, conventions, security>

   ### Missing tests
   - <function> in <file> — no test coverage found

   ### Recommendations
   1. <actionable>

   VERDICT: Ship / Fix-then-ship / Block
   ```

## Tips

- Use `semantic_search_nodes_tool` to find related code the diff might have missed.
- This skill complements `/pre-ship-audit` — use both for high-stakes merges.
