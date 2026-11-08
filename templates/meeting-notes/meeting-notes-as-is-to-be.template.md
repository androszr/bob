---
title: "AS IS → TO BE workshop: {Topic}"
type: meeting
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — what the meeting was about and its main takeaway}"
---

# Template: Meeting / workshop summary (Meeting Notes)

> **How to use:**
> - Use this template to summarise **discovery workshops / AS IS → TO BE process-mapping sessions** with the business (e.g. field service technicians, dispatchers, regional managers).
> - The AS IS / TO BE / Constraints sections are optional — remove them if the meeting was of a different kind (e.g. a status update, a decision meeting, Q&A only).
> - Fill it in from the audio transcript (`*.mp3.json`) or your own notes; keep quotes verbatim — they are strong evidence of what participants meant.
> - **Always add a key quote + its speaker next to pain points and requirements** — it protects against reinterpretation during analysis.
> - **Convert relative dates to absolute ones** (e.g. "in January" → "January 2026") so the document still reads correctly later.
> - Put recording timestamps in the "Attachments / sources" section — they help you get back to the context without listening to the whole thing again.
> - Name the file by convention: `workshop-{topic-kebab}-summary.md` or `meeting-{topic-kebab}-{YYYY-MM-DD}.md`.

> **When to use:**
> - after workshops with the business (gathering needs, mapping the AS IS process, defining the TO BE)
> - after discovery / Q&A sessions with end users
> - after decision meetings whose outcomes must feed further analysis / refinement
> - as input for later user stories, Jira tasks and UX briefs
>
> **When NOT to use:**
> - if it was a 1-on-1 / 1-on-2 interview with a single interviewee (no process mapping with several participants) → use `meeting-notes-discovery-interview.template.md`
> - if it was an observation at the workplace / shadowing → use `meeting-notes-contextual-inquiry.template.md`
> - if it was an evaluation interview about an existing product → use `meeting-notes-product-feedback-interview.template.md`
> - if the only goal was to make a decision → use `meeting-notes-decision.template.md`
> - if it was a status update / sync → use `meeting-notes-status-update.template.md`
> - if it was the start of a new initiative → use `meeting-notes-kickoff.template.md`
> - if it was a wrap-up of a completed stage → use `meeting-notes-retrospective.template.md`

---

# {Meeting type}: {Topic} — summary

**Source file:** [{transcript file name}]({link-to-file})
**Meeting date:** {YYYY-MM-DD}
**Duration:** ~{NN} min
**Initiative / Epic:** {initiative name, context of where it came from — e.g. pilot user council, ticket PRJ3-XXX}
**Meeting goal:** {one sentence: what exactly we wanted to get out of this meeting — e.g. gathering business needs for the AS IS and TO BE of process X}

## Participants (roles)

- **{Group 1, e.g. Field service technicians / Dispatchers}:** {Name (role, brief context — seniority, expertise, role in the meeting)}, {Name (role)}
- **{Group 2, e.g. Acme side / product}:** {Name (PO/analysis/UX/Technology)}, {Name (role)}
- **Mentioned (people not present but referred to):** {Name — context, agreement, open thread}

---

## TL;DR (in one paragraph)

_{Max. 5–8 sentences. What is the core of it? What problem? What key finding? What TO BE direction? What was explicitly parked for a separate thread? Write it so that someone who was not in the meeting understands the direction without reading the rest of the document.}_

---

## AS IS — canonical model of {documents / process / domain}

_{Optional — when the meeting mapped a process. If it fits, use an ASCII diagram (flow of documents, states, roles). Goal: one glance and you know how it works today.}_

```
{Step 1} → {Step 2} → {Step 3}
              │
              ├──> {Variant A — e.g. a side document}
              │
              └──> {Variant B}
                    ├─ {Sub-variant}
                    └─ {Sub-variant}
```

**Key invariant / business rule:** `{formal notation, e.g. closed work order == sum(work on the service report) ± correction}`

---

## AS IS — the "{operation name as the business calls it}" process (step by step)

_{Fill in if someone gave a live demo or described the flow. A numbered list of actions + what the user sees + where they reach "outside the system" (printed service report, calculator, Excel, other apps).}_

1. {Step 1 — where they go in, what they click}
2. {Step 2 — what they enter, which field}
3. {Step 3 — what the system shows, what is missing from the view}
4. {Step N — finalisation, document status}

**Cycle / volume:**

- {Persona A}: {frequency — daily / weekly / monthly}
- {Persona B}: {frequency}
- Volume: {e.g. ~NNN cases per month / a work order with NN line items}

## AS IS — {second stage / variant / related process — optional}

_{Optional — if an important parallel thread came up in the meeting that is not the main topic. Summarise it in 3–6 points and state its status clearly: closed / parked for a separate thread / needs a decision.}_

- {What it is / what it is for}
- {How it works today}
- {Real incidents caught thanks to it — with evidence, e.g. "technician in March — work order closed twice"}

**Status in the workshop:** {e.g. "raised as the next, equally important stage — participants agreed to come back to it separately"}

---

## AS IS — problems / "pain points" (list of clearly articulated ones)

_{Each pain point = one sentence + a verbatim quote or evidence from the workshop. No interpretation. Number them — it makes it easier to refer to them in tasks later: "solves pain point #3, #7".}_

| # | Pain point | Quote / evidence from the workshop |
|---|-----|---------------------------|
| 1 | {Short description of the pain point} | {Name: "verbatim quote"} |
| 2 | {Short description of the pain point} | {Name: "verbatim quote" / description of the situation} |
| 3 | {Short description of the pain point} | {Name: "verbatim quote"} |

---

## TO BE — expectations captured during the workshop

> **Key quote ({Name}):** "{verbatim quote that best captures the target direction — often said by a participant as a spontaneous summary}".

### Functional requirements (ordered by priority from the workshop)

1. **{Requirement 1 — short name}** — {description of what exactly should happen; edge conditions; participant quote, if helpful}.
2. **{Requirement 2}** — {description}:
   - {sub-condition},
   - {sub-condition},
   - {sub-condition}.
3. **{Requirement 3}** — {description}.
4. **{Requirement N}** — {description + any clarifications in a quote}.

### Non-functional requirements

- {Scale / volume — e.g. work orders with hundreds of line items}
- {Performance / response time — e.g. data available immediately}
- {Accessibility / UX for new users}
- {Security / compliance / regulations}

---

## Constraints / "things that will not change"

_{Very important — participants explicitly stated what IS a business rule and is NOT up for negotiation in the TO BE. Skip this and you risk a design proposal that gets rejected in 5 minutes.}_

- **{Rule 1}** — {why we are not changing it}:
  1. {reason 1},
  2. {reason 2}.
- **{Rule 2}** — {justification}.
- {Dual / multi-variant nature of the process — the TO BE solution must handle **all** variants}.

---

## Open questions (to address in process mapping / with the analyst)

_{Everything that could not be settled in the meeting. Each question should have an owner for the answer — or be clearly marked "for analysis" / "for technology" / "for the business".}_

1. {Question 1 — concrete, phrased so it can be answered yes/no or with a number}
2. {Question 2}
3. {Question N}

---

## Action items (next steps agreed in the workshop)

| # | What | Who | Due |
|---|----|-----|--------|
| 1 | {Concrete action — e.g. map the process with the analyst} | {Name / roles} | {YYYY-MM-DD or "before the next meeting"} |
| 2 | {Action} | {Name} | {due date} |
| 3 | {Action} | {Name} | {due date} |

---

## Pointers for the AS IS analysis

_{What exactly the analyst should close out after this meeting — so they don't start from scratch. Bulleted, short.}_

- Map the **BPMN diagram / sequence** of the "{name}" process with its artefacts: {list of documents / entities mentioned in the workshop}.
- Inventory the **places where the user reaches "outside the system"** — {examples from the workshop: printed service report, calculator, Excel, email}.
- Measure **volumes** (if possible from data): {what exactly — e.g. number of documents per persona per month, average number of line items, distribution of discrepancy types}.
- Catalogue the **classes of {cases / discrepancies / variants}** with examples from the workshop as test cases.
- Record the **external dependencies** — {e.g. arrangements with specific people, integrations with external systems}.

## Pointers for the TO BE design

_{Short hints for UX / the Product Designer. Not a design — directions.}_

- The user's default screen: {idea for the "happy path" — e.g. a list with a match flag, most line items auto-matched}.
- For {exceptions / discrepancies}: {detail with a type label + link to the source document + "one-click" action}.
- A mechanism for {duplicate detection / X} based on: {attributes, confidence levels}.
- New-user onboarding: {guided mode / tutorial — explicitly raised by participants as valuable}.
- {Source of truth} for {entity}: design the integration so the need for {today's workaround} disappears.

---

## Attachments / sources

- Audio transcript (segments with timestamps and speakers): [{file name}]({link})
- Key moments in the recording:
  - {MM:SS} — {who and about what — e.g. "introduction, meeting goal"}
  - {MM:SS} — {who and about what}
  - {MM:SS} — {who and about what}
  - {MM:SS} — {agreeing on next steps}
- Related documents:
  - {link to the user story / Jira task / previous summary, if this is a continuation}
  - {link to the ux-brief / task.template, if they came directly out of the workshop}
