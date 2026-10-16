---
name: pre-ship-audit
description: Comprehensive pre-commit or pre-push audit of staged or branch changes.
  Use when the user says "audit my changes", "review before commit", "ready to push",
  "ship check", "sprawdz zmiany", "przed pushem sprawdz", or invokes /pre-ship-audit.
  Runs ruff + pytest first (fail fast), then dispatches three specialist auditors
  (code quality, security, repo conventions) in parallel, synthesizes a single
  prioritized report with a clear VERDICT.
---

# Pre-Ship Audit

## Scope detection

Determine diff scope before anything else:

- Default (no flag): **pre-commit** — `git diff --staged`
- `--push` flag: **pre-push** — `git diff @{u}..HEAD`; fall back to `git diff main..HEAD` if no upstream
- `--branch` flag: **full branch** — `git diff main..HEAD`

If the detected scope yields an empty diff, stop and tell the user.

Also collect: `git status --short`, full list of changed file paths.

## Phase 1 — Deterministic gates (run in parallel, fail fast)

Run all applicable checks. Surface failures verbatim with file:line context.
**If any gate fails, stop here — do not proceed to Phase 2.**

| Gate | Condition |
|---|---|
| `ruff check <changed .py files>` | Any `.py` file in scope |
| `python3 -m pytest tests/ -x -q` | Any `.py` file in scope or `tests/` touched |
| Full `python3 -m pytest tests/` | `--push` or `--branch` scope only |

Never invoke `--no-verify` on any git hook.

## Phase 2 — Judgment audit (parallel)

Dispatch in a **single message** with Agent tool calls (they run concurrently). Each agent receives:
- The full diff text
- The list of changed files
- A pointer to `.claude/skills/pre-ship-audit/references/` files
- The output schema from `references/output-schema.md`

**Always dispatch:** `code-quality-auditor`, `security-auditor`, `repo-conventions-auditor`.

**Conditionally dispatch** `qa-automation-auditor` in diff mode — only when the diff touches
`scripts/` Python files or `tests/`. Skip for docs-only, config-only, or knowledge-only diffs.

## Phase 3 — Synthesis

Collect all reports. Apply:

1. **Dedupe**: same file:line flagged by multiple agents → merge, cite both lenses.
2. **Calibrate severity**: disagreement → take the higher. Explain why.
3. **Rank**: Blockers → Should-fix → Nits. Within each band: alphabetical by file.
4. **Render** per `references/output-schema.md`.
5. End with a single VERDICT line.

## Phase 4 — Persist the report

Write synthesized report to `audits/pre-ship-audit-<YYYY-MM-DD>-<HHMM>.md` (`audits/` is gitignored and local only):
```bash
mkdir -p audits
```

File contents: rendered Phase 3 report with heading `# Pre-Ship Audit Report` and metadata block
(`Scope`, `Changed files: N`, `Generated: <ISO timestamp>`, `Branch: <git branch>`, `HEAD: <short SHA>`).

After writing, print the absolute path and VERDICT line. Do NOT also dump the full report inline.

## Phase 5 — Optional implementation

After writing the audit file, print:
`Reply "apply" to implement BLOCKER + SHOULD-FIX items, or "ignore" to stop.`

If the user replies "apply": spawn a `general-purpose` Agent (model: sonnet) with:
- Path to the audit file (it must Read it itself)
- Instruction: implement every BLOCKER and SHOULD-FIX with a concrete edit. Skip Nits.
- After edits: run `ruff check` and relevant `pytest` subset.
- Report back: what was applied, what was skipped, gate results.

Relay the subagent summary and stop. Do not re-run the audit.
