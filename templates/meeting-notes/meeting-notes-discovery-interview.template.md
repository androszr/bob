---
title: "Discovery interview: {Name / Role}"
type: meeting
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — what the meeting was about and its main takeaway}"
---

# Template: User interview / discovery (Meeting Notes)

> **What this template is for (read carefully before you choose it):**
> - A **qualitative interview / user interview / discovery session** with 1–3 interviewees (most often from the business: field service technician, dispatcher, app user, domain expert, stakeholder).
> - Goal: **to understand the interviewee's perspective** — their work, pain points, context, vocabulary. **We are not formally mapping an AS IS → TO BE process** (if the interview grew into a mapping workshop, use the as-is-to-be template).
> - **A classic qualitative interview, with no process observation and no specific product in the background** — e.g. "tell me what a typical day looks like for you", "what frustrated you at work recently". If the conversation was "how do you use app X", or you observed the interviewee at their workplace — see "When NOT to use".
> - Output: insights + quotes + pain points + the interviewee's ideas — raw research material for further analysis.
>
> **When NOT to use:**
> - if it was an **observation at the workplace** (contextual inquiry / shadowing / field visit to a branch/office) → use `meeting-notes-contextual-inquiry.template.md`
> - if it was an **evaluation interview about an existing product** ("how do you use app X, what works, what hurts") → use `meeting-notes-product-feedback-interview.template.md`
> - if it was a process-mapping workshop (several participants, a live demo, a document diagram) → use `meeting-notes-as-is-to-be.template.md`
> - if it was a team status update / sync → use `meeting-notes-status-update.template.md`
> - if the goal was to make a decision → use `meeting-notes-decision.template.md`
> - if it was the start of a new initiative → use `meeting-notes-kickoff.template.md`
> - if it was a wrap-up of a completed phase → use `meeting-notes-retrospective.template.md`
>
> **How to use:**
> - **Verbatim quotes** — they are the foundation of a qualitative interview. No paraphrasing, no interpretation inside a quote.
> - Each pain point / insight → a short description + a verbatim quote with the interviewee's name. Number them — easier to refer to later.
> - Keep **what the interviewee said** apart from **what we conclude from it** (a separate "interpretation / hypotheses" section).
> - **Convert relative dates to absolute ones**.
> - Name the file: `interview-{interviewee-role-kebab}-{YYYY-MM-DD}.md` or `discovery-{topic-kebab}-{YYYY-MM-DD}.md`.

---

# Interview: {Interviewee's role / name} — {Topic / area}

**Source file:** [{transcript file name}]({link})
**Interview date:** {YYYY-MM-DD}
**Duration:** ~{NN} min
**Format:** {1-on-1 / 1-on-2 / group session of N people / phone / online meeting / on-site}
**Initiative / context:** {name of the initiative under which we are doing discovery}
**Interview goal:** {one sentence — e.g. understand how a dispatcher handles rescheduling customer visits}

## Interviewees

- **{Name (role)}** — {time in role, brief context: type of work orders, region, experience}
- **{Name (role)}** — {context, if there were more people}

## Interviewers

- {Name (PO / UX / Researcher / Analyst)}, {Name (role)}

---

## TL;DR (in one paragraph)

_{Max. 4–6 sentences. Who, about what, the most important takeaways, which key quote captures the essence, what the next action is. Write it so a teammate doesn't have to listen to the recording.}_

---

## Key quote (the strongest moment of the interview)

> **{Name}:** "{verbatim quote — the one that best captures the interviewee's tone, pain point or aspiration}"
>
> _Context:_ {what was going on in the conversation when this was said}

---

## Interview topics (interview structure)

_{One subheading for each major topic covered. Under each: a 3–6 sentence summary + 1–2 verbatim quotes. Follow the order of the conversation or regroup by theme — whichever reads better.}_

### {Topic 1 — e.g. "What does a typical working day look like for you"}

{A short summary of what the interviewee said — 3–6 sentences.}

> **{Name}:** "{verbatim quote}"

### {Topic 2 — e.g. "What annoys you most about the current tool"}

{Summary.}

> **{Name}:** "{quote}"
> **{Name}:** "{quote}"

### {Topic 3 — …}

{…}

---

## Pain points / problems (list of clearly articulated ones)

_{Each pain point = one sentence + a quote. No interpretation. Number them.}_

| # | Pain point | Quote / evidence |
|---|-----|---------------|
| 1 | {short description of the pain point} | {Name}: "{verbatim quote}" |
| 2 | {short description of the pain point} | {Name}: "{quote or description of an incident}" |
| 3 | {…} | {…} |

---

## The interviewee's ideas / suggestions

_{What they proposed themselves — even if it is not feasible. Don't dismiss it up front — sometimes a naive idea hides a real need.}_

1. **{Idea 1}** — {description + quote}
2. **{Idea 2}** — {description + quote}
3. **{Idea 3}** — {description}

---

## What the interviewee would **NOT** do / will not agree to

_{A very valuable section — explicitly stated constraints, "red lines". Skip it and you risk a proposal that gets rejected.}_

- **{Constraint 1}** — {why, a quote if helpful}
- **{Constraint 2}** — {…}

---

## The interviewee's vocabulary (glossary)

_{Terms the interviewee used differently from us, or that are specific to their domain. Important — using them in the UI / communication lowers the barrier to adoption.}_

| Interviewee's term | What they mean by it | What we call it |
|-----------------|---------------------|--------------------|
| {"{term}"} | {definition as they understand it} | {our equivalent / none} |
| {"{term}"} | {…} | {…} |

---

## Team interpretation / hypotheses (kept apart from the quotes)

_{Here you can interpret. Clearly marked as our interpretation, not the interviewee's words. This is input for further analysis.}_

- **Hypothesis 1:** {…} — _supported by_: pain points #2, #5; quote from topic "X"
- **Hypothesis 2:** {…}
- **Worth validating in later interviews:** {…}

---

## Open questions

1. {Question 1 — what we didn't get to ask / what needs clarifying}
2. {Question 2}
3. {Question N}

---

## Action items

| # | What | Who | Due |
|---|----|-----|--------|
| 1 | {e.g. send the findings to the PO} | {Name} | {YYYY-MM-DD} |
| 2 | {e.g. invite another interviewee from a different persona} | {Name} | {YYYY-MM-DD} |
| 3 | {e.g. go back to the interviewee with a prototype} | {Name} | {YYYY-MM-DD} |

---

## Attachments / sources

- Audio transcript: [{file name}]({link})
- Key moments:
  - {MM:SS} — {topic / key quote}
  - {MM:SS} — {"aha" moment}
  - {MM:SS} — {pain point #N}
- Related interviews / documents:
  - [{another interview with the same persona / initiative}]({link})
  - [{user story / brief}]({link})
