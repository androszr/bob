---
title: "Kickoff: {Initiative name}"
type: meeting
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — what the meeting was about and its main takeaway}"
---

# Template: Initiative / project kickoff (Meeting Notes)

> **What this template is for (read carefully before you choose it):**
> - A **kickoff** meeting — the formal start of a new initiative / project / epic / large workstream.
> - Telltale signs: "we're starting topic X", "first meeting about the initiative", "presenting the scope and roles", "agreeing on the goal and what success looks like".
> - Goal of the document: **to lock in a shared understanding** of the goal, scope, roles, risks and timeline right at the start — to avoid diverging interpretations 3 months later ("I thought we were doing X, not Y").
> - Often paired with a brief / project charter — this template is its shorter, "notes" version.
>
> **When NOT to use:**
> - if the initiative is already under way and this was another status update → use `meeting-notes-status-update.template.md`
> - if the goal was to settle a single decision → use `meeting-notes-decision.template.md`
> - if we mapped an AS IS / TO BE process → use `meeting-notes-as-is-to-be.template.md`
> - if it was a qualitative / discovery interview with 1–3 people → use `meeting-notes-discovery-interview.template.md`
> - if it was an observation at the workplace / shadowing → use `meeting-notes-contextual-inquiry.template.md`
> - if it was an evaluation interview about an existing product → use `meeting-notes-product-feedback-interview.template.md`
> - if it was a wrap-up of a completed stage → use `meeting-notes-retrospective.template.md`
>
> **How to use:**
> - **Define success measurably** — if you can't say how we will know it worked, don't start the initiative.
> - **Scope: in / out** — list explicitly what we are NOT doing. Leaving this section out guarantees scope creep.
> - **Record assumptions and risks even if they are "obvious"** — in six months they will stop being obvious.
> - **Convert relative dates to absolute ones**.
> - Name the file: `kickoff-{initiative-kebab}-{YYYY-MM-DD}.md`.

---

# Kickoff: {Initiative name} — {YYYY-MM-DD}

**Source file:** [{transcript / notes file name}]({link})
**Meeting date:** {YYYY-MM-DD}
**Duration:** ~{NN} min
**Initiative / Epic:** {name + link to the epic in Jira / Confluence}
**Initiative sponsor:** {Name (role) — budget holder / decision-maker}
**Product Owner:** {Name}
**Tech Lead:** {Name}

## Participants

- **Sponsor / decision-makers:** {Name (role)}, {Name (role)}
- **Delivery team:** {Name (role)}, {Name (role)}, {Name (role)}
- **Business stakeholders:** {Name (role)}, {Name (role)}
- **Mentioned (relevant people who were not present):** {Name — context, when they will be brought in}

---

## TL;DR (in one paragraph)

_{Max. 5–8 sentences. Why we are starting → what problem we are solving → for whom → how we will recognise success → when we want to finish → who is driving it. Write it so that someone joining the initiative a month from now understands everything from it.}_

---

## Initiative goal

### Problem / opportunity

_{2–4 sentences. What exactly is happening (or not happening) that we need to launch this initiative? Why now?}_

### Business goal

_{In one sentence — what we want to deliver to the business. "So that [persona] can [do X] faster / better / at all".}_

### Value hypothesis

_{What we expect to happen once we deliver the solution. "We believe that [change] will lead to [effect]; we will measure it through [metric]."}_

---

## Definition of success (how we will know it worked)

_{Measurably. No "better", "faster", "more efficient" without numbers. If we don't have a baseline yet — write "to be measured in the discovery phase".}_

| # | Success metric | Baseline (today) | Target | Measurement date |
|---|------------------|-----------------|--------|----------------|
| 1 | {e.g. time to handle process X} | {NN min} | {NN min} | {YYYY-MM-DD, e.g. 3 months after rollout} |
| 2 | {…} | {…} | {…} | {…} |
| 3 | {…} | {…} | {…} | {…} |

**Anti-metrics (what we watch so that it does NOT drop):** {e.g. customer NPS, adoption of another feature, the time of another process}

---

## Scope

### In scope (we are doing)

1. {Scope item 1}
2. {Scope item 2}
3. {Scope item 3}

### Out of scope (knowingly NOT doing in this initiative)

_{A critical section — it protects against scope creep. List explicitly the things someone might want to add along the way.}_

1. {What we are parking + why — e.g. "integration with system Z — parked until the next iteration because of a dependency on team Q"}
2. {…}

### May come into scope conditionally

_{Things we will let in if there is time / budget left. A clear "nice-to-have", not a commitment.}_

- {…}
- {…}

---

## Personas / who we are doing this for

_{The primary and secondary personas this affects. Without this section the project will end with a generic solution for nobody.}_

- **Primary:** {persona — who they are, what their key job-to-be-done is}
- **Secondary:** {persona}
- **Impact on other groups:** {e.g. operations, support — how the change will affect them}

---

## Assumptions (that the plan is built on)

_{If any of these assumptions stops being true — the plan needs to be re-reviewed.}_

1. {Assumption 1 — e.g. "the integration with X will be available from Q2 2026"}
2. {Assumption 2 — e.g. "we have budget for external vendor Y"}
3. {Assumption N}

---

## Constraints / "things that will not change"

_{Named in the meeting as hard constraints — regulations, board decisions, integrations we are not touching.}_

- **{Constraint 1}** — {why}
- **{Constraint 2}** — {why}

---

## Risks and mitigation

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|--------|--------------------|-------|-----------|------------|
| 1 | {risk description} | {L/M/H} | {L/M/H} | {what we do so it doesn't materialise} | {Name} |
| 2 | {…} | {…} | {…} | {…} | {…} |
| 3 | {…} | {…} | {…} | {…} | {…} |

---

## Timeline (milestones)

_{High level — not a detailed plan, but a contract on the main checkpoints.}_

| Milestone | What we deliver | Planned date |
|-----------|----------------|----------------|
| M1 — {name} | {e.g. discovery closed, go/no-go decision} | {YYYY-MM-DD} |
| M2 — {name} | {e.g. MVP rolled out to the pilot} | {YYYY-MM-DD} |
| M3 — {name} | {e.g. full rollout} | {YYYY-MM-DD} |
| M4 — {name} | {e.g. first measurement of success metrics} | {YYYY-MM-DD} |

**Next checkpoint:** {YYYY-MM-DD} — {what exactly should be ready by then}

---

## Roles and responsibilities (RACI)

_{List the key roles — who is responsible for what. It doesn't have to be a full RACI matrix, but every role on the team should know what is expected of it.}_

| Role | Person | Main responsibility |
|------|-------|--------------------------|
| Sponsor | {Name} | Budget, escalation, final decision |
| Product Owner | {Name} | Priorities, scope, contact with the business |
| Tech Lead | {Name} | Architecture, quality, technical decisions |
| UX / Designer | {Name} | Design, qualitative research |
| Analyst | {Name} | Requirements, process mapping, data |
| QA | {Name} | Test strategy, quality |
| {other role} | {Name} | {…} |

---

## External dependencies

_{What we need from people / teams / systems outside our team for the initiative to succeed.}_

- **{Team / system 1}** — {what we need, by when, who our contact on that team is}
- **{Team / system 2}** — {…}

---

## Open questions

1. {Question 1 — who answers, by when}
2. {Question 2}
3. {Question N}

---

## Action items (after the kickoff)

| # | What | Who | Due |
|---|----|-----|--------|
| 1 | {e.g. prepare a brief / user story for refinement} | {Name} | {YYYY-MM-DD} |
| 2 | {e.g. set up the initiative in Jira / Confluence} | {Name} | {YYYY-MM-DD} |
| 3 | {e.g. schedule the first workshop with the business} | {Name} | {YYYY-MM-DD} |
| 4 | {e.g. agree how to measure the metrics baseline} | {Name} | {YYYY-MM-DD} |

---

## Attachments / sources

- Transcript: [{file name}]({link})
- Related documents:
  - [{brief / project charter}]({link})
  - [{epic in Jira}]({link})
  - [{the initiative's Confluence folder}]({link})
  - [{earlier analyses / discovery we are building on}]({link})
