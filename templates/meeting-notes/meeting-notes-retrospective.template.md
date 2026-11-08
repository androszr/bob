---
title: "Retrospective: {Sprint / stage name}"
type: meeting
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — what the meeting was about and its main takeaway}"
---

# Template: Retrospective / stage wrap-up (Meeting Notes)

> **What this template is for (read carefully before you choose it):**
> - A **retrospective** meeting — we look back on a completed period (sprint, quarter, project stage, rollout, incident) and draw lessons from it.
> - Typical formats that fit this template: "start / stop / continue", "what went well / what went wrong / what to change", "4 L's (liked, learned, lacked, longed for)", incident post-mortem.
> - Goal: **lessons learned** + concrete process improvements for the next stage. It is NOT a status update ("what we did") but a reflection ("why it turned out this way and what we take away from it").
>
> **When NOT to use:**
> - if it was a sync reporting progress → use `meeting-notes-status-update.template.md`
> - if the goal was to settle a specific decision → use `meeting-notes-decision.template.md`
> - if it was a discovery workshop with the business → use `meeting-notes-as-is-to-be.template.md`
> - if it was a qualitative interview with a user → use `meeting-notes-discovery-interview.template.md`
> - if it was an observation at the workplace / shadowing → use `meeting-notes-contextual-inquiry.template.md`
> - if it was an evaluation interview about an existing product → use `meeting-notes-product-feedback-interview.template.md`
> - if it was the start of a new stage → use `meeting-notes-kickoff.template.md`
>
> **How to use:**
> - **Separate observations from interpretation** — first what happened, then why (a hypothesis), then what we take away from it.
> - Every action item must have an owner and a due date — otherwise the retrospective turns into venting frustration with no effect.
> - If the retrospective was an incident post-mortem — also fill in the "Incident timeline" section.
> - **Blame-free**: describe events, not fault. Names are OK with facts ("X deployed"), not OK with judgements ("X made a mistake").
> - **Convert relative dates to absolute ones**.
> - Name the file: `retro-{stage-kebab}-{YYYY-MM-DD}.md` or `post-mortem-{incident-kebab}-{YYYY-MM-DD}.md`.

---

# Retrospective: {Stage / Sprint / Initiative / Incident} — {YYYY-MM-DD}

**Source file:** [{transcript / notes file name}]({link})
**Meeting date:** {YYYY-MM-DD}
**Duration:** ~{NN} min
**Retrospective format:** {start-stop-continue / went-well-wrong-change / 4L / post-mortem / other}
**Period under review:** {from YYYY-MM-DD to YYYY-MM-DD — e.g. sprint 23, Q1 2026, rollout of X}
**Initiative / Epic / Incident:** {name, context, link to the epic/ticket if relevant}

## Participants

- **{Group 1, e.g. Delivery team}:** {Name (role)}, {Name (role)}
- **{Group 2, e.g. Stakeholders}:** {Name (role)}
- **Facilitator:** {Name}
- **Absent (their perspective gathered asynchronously):** {Name — through which channel}

---

## TL;DR (in one paragraph)

_{Max. 4–6 sentences. What the stage was → overall assessment → the 1–2 biggest successes → the 1–2 biggest lessons → the most important process change we are introducing.}_

---

## What went well (continue)

_{Things we want to repeat. Not generalities ("good atmosphere") — specifics that can be repeated in the next stage.}_

1. **{Success 1}** — {description + a quote or example: "thanks to this we managed to X"}
2. **{Success 2}** — {description}
3. **{Success N}** — {description}

---

## What didn't work (problem)

_{Specific situations, decisions, mechanisms that got in the way. Don't make it personal — describe facts, not fault.}_

| # | What | When / context | Effect |
|---|----|-------------------|--------|
| 1 | {what happened / what was missing} | {when, at which stage} | {what suffered because of it} |
| 2 | {…} | {…} | {…} |
| 3 | {…} | {…} | {…} |

---

## Ideas for improvement (start / change)

_{What we start doing / what we change. Each idea should link to a specific problem above — otherwise we drift into abstraction.}_

1. **{Idea 1}** — addresses problem #{number} → {description of the process change}
2. **{Idea 2}** — addresses problem #{number} → {…}
3. **{Idea N}** — {…}

---

## Lessons learned (cross-cutting)

_{What we take away beyond the single stage — conclusions about the way we work in general. Short, in a few words.}_

- {Lesson 1 — e.g. "we don't start implementation without approved UX mockups, because it leads to rework"}
- {Lesson 2}
- {Lesson N}

---

## Incident timeline (ONLY for a post-mortem)

_{Fill in only if the retrospective was a post-mortem of a production incident. Otherwise — delete this section.}_

| Time | What happened | Who detected / responded | Decision / action |
|------|--------------|--------------------------|------------------|
| {YYYY-MM-DD HH:MM} | {trigger — what set off the incident} | {monitoring / user / Name} | {…} |
| {YYYY-MM-DD HH:MM} | {detection} | {Name} | {…} |
| {YYYY-MM-DD HH:MM} | {mitigation} | {Name} | {…} |
| {YYYY-MM-DD HH:MM} | {resolution} | {Name} | {…} |

**Detection time (from trigger to detection):** {NN minutes}
**Mitigation time (from detection to mitigation):** {NN minutes}
**Resolution time (from trigger to full resolution):** {NN minutes/hours}
**User impact:** {how many users, which features, for how long}
**Root cause:** {in one sentence — what the actual cause was}
**Contributing factors:** {what additionally enabled / worsened the problem}

---

## Stage metrics (if measured)

_{Optional. Actual vs. planned values.}_

| Metric | Plan | Result | Comment |
|---------|------|-------|-----------|
| {e.g. velocity} | {NN} | {NN} | {…} |
| {e.g. % of tickets delivered} | {NN%} | {NN%} | {…} |
| {e.g. number of bugs after rollout} | {<NN} | {NN} | {…} |

---

## Action items (who, what, when)

| # | What | Who | Due | Related problem |
|---|----|-----|--------|--------------------|
| 1 | {concrete process action} | {Name} | {YYYY-MM-DD} | #{problem number} |
| 2 | {…} | {Name} | {YYYY-MM-DD} | #{…} |
| 3 | {…} | {Name} | {YYYY-MM-DD} | #{…} |

---

## Team sentiment / mood (optional)

_{Briefly — how the team felt during this stage. Helpful for watching burnout / motivation trends over time.}_

- **Energy:** {low / medium / high}
- **Confidence in the direction:** {low / medium / high}
- **Main sources of frustration:** {1–3 keywords}
- **Main sources of satisfaction:** {1–3 keywords}

---

## Attachments / sources

- Transcript: [{file name}]({link})
- Related:
  - [{retrospective from the previous stage}]({link}) — trend comparison
  - [{board / dashboard with metrics}]({link})
  - [{post-mortem / RCA}]({link}) — if it concerns an incident
