---
name: commit-message
description: Analyze the current git diff and propose Conventional-Commits-style
  commit message(s). Assesses whether the diff is cohesive (one commit) or
  spans multiple concerns (split proposal) and lets the user choose. Use when
  the user says "suggest a commit message", "zaproponuj commit", "jak nazwac commit",
  "draft commit msg", or invokes /commit-message. Runs on Haiku via a subagent —
  cheap and fast. Proposes only; does not commit unless the user confirms.
---

# Commit Message

Propose commit message(s) for the current changes. **Do not commit until the user confirms.**

## Args

Optional scope arg: `staged` (default if anything is staged), `unstaged`, `all`.

## Steps

1. Quick precheck with `git status --short`. If the working tree is clean, tell the user "No changes to describe." and stop.

1a. **Branch guard.** Run `git rev-parse --abbrev-ref HEAD`. If the current branch is `main` or `master`, warn the user and prompt:
   ```
   Options:
     branch <name>  — create <name>, carry changes over, commit there
     branch         — I'll suggest a name from the diff
     stay           — commit on main anyway (I'll confirm once more)
     no             — stop
   ```
   - On `branch [<name>]`: derive slug if no name given. Run `git checkout -b <name>`. Confirm, then continue.
   - On `stay`: re-confirm once, then continue.
   - On `no`: stop.
   Skip guard if branch is anything other than `main`/`master`.

2. Pick scope (honor arg, or auto-detect from `git diff --staged --quiet`).

3. Spawn the **commit-message-writer** subagent (Haiku):
   ```
   Agent({
     subagent_type: "commit-message-writer",
     description: "Draft commit message",
     prompt: "Scope: <staged|unstaged|all>. Draft a commit message per your instructions."
   })
   ```

4. If agent returns `NO_CHANGES`, tell the user and stop.

5. Parse response. Two shapes: single message, or SINGLE + SPLIT proposal.

6. Present to user:
   - Single: show in fenced block. Prompt: `Run git commit with this message? (yes / edit / no)`
   - With split: show both. Prompt: `Choose: (single / split / edit / no)`

7. **On `yes` / `single`**: `git commit` via HEREDOC + `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`. Stage files first if scope was `unstaged` or `all` (explicit paths from `git status`, never `-A`).

8. **On `split`**: execute sequence. For each commit: `git add <files>` (explicit paths) then `git commit` via HEREDOC + Co-Authored-By. Stop on hook failure — surface error, do not continue. After sequence: `git status` + `git log -N --oneline`.

9. **On `edit`**: capture revised message, re-present, loop until yes/split/no.

## Rules

- Never `--no-verify`. Never `--amend` unless user explicitly asks.
- Subagent stays on Haiku — do not run analysis in main session.
- Every changed file must appear in exactly one split group (verify before executing split).
