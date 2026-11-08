---
title: "Decision meeting: {Topic}"
type: meeting
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — what the meeting was about and its main takeaway}"
---

# Template: Decision meeting (Meeting Notes)

> **What this template is for (read carefully before you choose it):**
> - A meeting whose goal was **to make a specific decision** — choosing an option, approving a direction, resolving a dilemma, accepting a proposed solution.
> - Telltale signs: someone said things like "we need to decide…", "are we going with A or B", "we need sign-off", "let's agree on the approach".
> - The document's main output: **an unambiguous record of the decision** + its rationale + what happens next. The document also serves as an ADR (architectural decision record) or PDR (product decision record) for posterity.
>
> **When NOT to use:**
> - if the meeting only reported progress → use `meeting-notes-status-update.template.md`
> - if it was a discovery workshop / process mapping → use `meeting-notes-as-is-to-be.template.md`
> - if the conversation was with a single interviewee / a qualitative interview → use `meeting-notes-discovery-interview.template.md`
> - if it was an observation at the workplace / shadowing → use `meeting-notes-contextual-inquiry.template.md`
> - if it was an evaluation interview about an existing product → use `meeting-notes-product-feedback-interview.template.md`
> - if it was a review of a completed stage → use `meeting-notes-retrospective.template.md`
> - if it was the start of a new initiative → use `meeting-notes-kickoff.template.md`
>
> **How to use:**
> - **The decision must be stated UNAMBIGUOUSLY** — no "probably", "more or less", "I guess so".
> - Record every option considered, even the rejected ones — it protects against being second-guessed later ("why didn't you consider X?").
> - For each option: arguments for/against + who raised them (key quote, if there was a discussion).
> - Mark **who the decision-maker was** (RACI: A — Accountable) — it is not always a consensus.
> - **Convert relative dates to absolute ones**.
> - Name the file: `decision-{topic-kebab}-{YYYY-MM-DD}.md` or `adr-{NN}-{topic-kebab}.md`.

---

# Decision: {Decision title — one sentence, unambiguous}

**Source file:** [{transcript / notes file name}]({link})
**Meeting date:** {YYYY-MM-DD}
**Duration:** ~{NN} min
**Initiative / Epic:** {name, context}
**Decision status:** {made / conditional — requires X / deferred / proposal awaiting approval}

## Participants (and their role in the decision per RACI)

- **Decision-maker (A — Accountable):** {Name (role)} — the person who formally approved it
- **Consulted (C — Consulted):** {Name (role)}, {Name (role)}
- **Carrying out the decision (R — Responsible):** {Name (role)}, {Name (role)}
- **Informed (I — Informed):** {Name / team — who needs to be told}

---

## TL;DR (in one paragraph)

_{Max. 4–6 sentences. What had to be decided → what options → what was chosen → why → what changes now. Someone who was not in the meeting should understand the decision without reading the rest.}_

---

## Decision

> **{Full statement of the decision — ideally in the present tense, as a statement of fact, not a recommendation. E.g. "We are migrating reporting from system X to Y by the end of Q3 2026."}**

**Effective date:** {YYYY-MM-DD or "immediately"}
**Validity horizon:** {when the decision is up for review — e.g. "after the first rollout" / "in 6 months" / "indefinitely"}

---

## Context — why we were making this decision at all

_{2–5 sentences. What changed in the environment that a decision was needed? What problem are we solving? What happens if we don't make the decision?}_

- **Problem / motivation:** {…}
- **Trigger (what prompted the need for a decision):** {…}
- **Cost of inaction:** {what exactly will suffer if we stay with the status quo}

---

## Options considered

### Option A: {name} — **{CHOSEN / rejected / deferred}**

- **Description:** {what exactly this option means}
- **For:** {arguments + optionally a quote: "{verbatim quote}" — {Name}}
- **Against:** {arguments + quote}
- **Cost / effort:** {rough estimate}
- **Risks:** {main ones}

### Option B: {name} — **{CHOSEN / rejected / deferred}**

- **Description:** {…}
- **For:** {…}
- **Against:** {…}
- **Cost / effort:** {…}
- **Risks:** {…}

### Option C: {name} — **{CHOSEN / rejected / deferred}**

_{Add more options as needed. If there was only one realistic option — keep one and say so plainly: "the other options were ruled out early on because of X".}_

---

## Rationale for the chosen option

_{Why this one won and not another. Concretely — which argument tipped the balance. If it was controversial — note who objected and why (this matters for people coming back to the document a year later).}_

- **Main argument:** {…}
- **What we are knowingly giving up by choosing this option:** {the trade-off we accepted}
- **Dissenting voices (if any):** {Name — "{quote}"} → {how they were addressed / why they were set aside}

---

## Assumptions the decision rests on

_{If any of these assumptions stops being true — the decision needs to be re-reviewed. This is a key section for future analysis.}_

1. {Assumption 1 — e.g. "the quarterly budget stays at X"}
2. {Assumption 2}
3. {Assumption N}

---

## Consequences — what changes

- **What we start doing:** {…}
- **What we stop doing:** {…}
- **Who needs to be informed:** {concretely — a message to team X, an email to Y}
- **What needs updating in the documentation:** {links / document names}
- **Impact on other initiatives / teams:** {…}

---

## Open questions (things we did not settle)

_{What we did not close — with an owner for the answer and a due date. Don't put rhetorical questions here — only real issues to address.}_

1. {Question 1} — answer: {Name}, due: {YYYY-MM-DD}
2. {Question 2} — answer: {Name}, due: {YYYY-MM-DD}

---

## Action items

| # | What | Who | Due |
|---|----|-----|--------|
| 1 | Communicate the decision {to whom, through which channel} | {Name} | {YYYY-MM-DD} |
| 2 | {implementation step 1} | {Name} | {YYYY-MM-DD} |
| 3 | {implementation step 2} | {Name} | {YYYY-MM-DD} |

---

## Attachments / sources

- Transcript: [{file name}]({link})
- Key moments in the recording:
  - {MM:SS} — {presentation of the problem}
  - {MM:SS} — {the moment the decision was made}
  - {MM:SS} — {dissenting voice / counter-argument}
- Related documents:
  - [{earlier decisions that this one overrides / complements}]({link})
  - [{analysis, brief, RFC}]({link})
