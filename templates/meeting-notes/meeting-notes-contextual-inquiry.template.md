---
title: "Field visit: {Topic}"
type: meeting
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — what the meeting was about and its main takeaway}"
---

# Template: Field visit / workplace observation (Meeting Notes)

> **What this template is for (read carefully before you choose it):**
> - A **contextual inquiry / shadowing / field visit** session — the researcher goes to the user and observes how they actually carry out the process in their own work environment (branch, office, parts warehouse, at the customer's site).
> - A hybrid of **observation + interview** — first we watch "what they really do", then we ask follow-up questions. Goal: to see the **real workarounds, personal rules and moments of frustration** you won't hear about in a remote conversation.
> - Output: raw observational material + quotes + the researcher's hypotheses. Input for user stories, UX briefs and product decisions.
>
> **When NOT to use:**
> - if it was an interview without observing the process (remote conversation, "how is work going for you?") → use `meeting-notes-discovery-interview.template.md`
> - if it was an evaluation interview about an existing product ("how do you use app X") → use `meeting-notes-product-feedback-interview.template.md`
> - if it was a process-mapping workshop with several participants → use `meeting-notes-as-is-to-be.template.md`
> - if it was a status update / sync → use `meeting-notes-status-update.template.md`
> - if the goal was to settle a decision → use `meeting-notes-decision.template.md`
>
> **How to use:**
> - **Separate observation from interpretation** — in the "Process observation" section record facts (what they clicked, what they said, in what order). Hypotheses and conclusions go in a separate section at the end.
> - **Verbatim quotes** — record every strong verbal moment as a quote with the interviewee's name.
> - **Note personal rules and workarounds** — this is the most valuable material from a visit (a sticky note next to the computer, their own clicking order, using another system "because you can't do it here").
> - **Note emotions and attitude** — tone of voice, frustrations, routine, confidence. This is context that a remote interview lacks.
> - **Convert relative dates to absolute ones** (e.g. "in January" → "January 2026").
> - YAML frontmatter per the folder's convention (`type: field-visit` / `site-visit`, location, segment, role, duration_min, tags, summary).
> - Name the file: `{YYYY-MM-DD}-{visit-type-kebab}-{site-id}.md` (e.g. `2026-04-07-field-visit-br-014.md`).

---

```yaml
---
type: field-visit
initiative: {initiative-name-kebab}
date: {YYYY-MM-DD}
{site-id}: {e.g. OD-014}
{location}: {e.g. site_city: City-A}
{segment / type}: {e.g. site_segment: industrial facility}
role: {technician / dispatcher / customer / manager / other}
interviewee: {First Last}
researcher: {Researcher's First Last}
duration_min: {NN}
tags: [{tag1}, {tag2}, {tag3}]
summary: "{2–4 sentences — who, the site context, the strongest insight, the main pain points}"
---
```

# Field visit — {Topic / Area}
## {Site ID} | {YYYY-MM-DD}

**Researcher:** {First Last}
**Duration:** approx. {NN} min

---

## Site / branch profile

_{Optional — fill in if the visit concerned a specific branch / customer / job site from the reference database. Paste the key fields from the database so the report becomes self-contained (the reader doesn't have to look up context in another system).}_

### Identity and status

| Field | Value |
|------|---------|
| No. / ID | `{identifier}` |
| Status | `{OPEN / other}` |
| {Owner / interviewee} | `{First Last}` |
| Contact | `{phone / email}` |

### Location and characteristics

| Field | Value |
|------|---------|
| Town / city | `{…}` |
| Address | `{…}` |
| Province / region | `{…}` |
| Floor area / size | `{…}` |
| Segment / site type | `{…}` |

### Support structure / indirect contacts

| Field | Value |
|------|---------|
| {Manager / account owner} | `{First Last}` |
| {Partner / supervisor} | `{First Last}` |
| {Director / region} | `{First Last}` |

---

## 1. Interviewee profile

| Question | Answer |
|---------|-----------|
| Time in role / in the organisation | {…} |
| Number of sites / branches they look after | {…} |
| {Operational context — e.g. warehouse size, team structure} | {…} |
| {Work rhythm — e.g. hours for taking work orders, hours for customer visits} | {…} |
| Where they carry out the observed process (home / office / branch / in the field) | {…} |
| How they prepare for the process (notebook / Excel / work phone / WhatsApp / other systems) | {…} |

---

## 2. Context before opening {the tool / app}

> _What does the interviewee do before going into the tool? Notebook, Excel, photos, WhatsApp with the team, work phone, other systems?_

{3–6 sentences describing the real setup. What they had open, what they had prepared on paper, what they relied on before going into the tool. This shows **what the tool leaves out** — because if the user does it somewhere else, it means they don't trust the tool or don't know how to do it there.}

---

## 3. Observation of the {process name} process

### Sequence of actions

{Describe step by step what the researcher **saw** — not what the interviewee said they do. Concretely: which menu they clicked, in what order they scrolled, where they paused longer. A numbered list or a paragraph, whichever reads better.}

### {Main interaction — e.g. quantity changes / data entry}

| Type of {change / action} | {Category / area} | Reason / interviewee's comment |
|----------------------|----------------------|----------------------------|
| {e.g. stock correction} | {…} | "{verbatim quote}" |
| {e.g. decrease} | {…} | {…} |
| {e.g. increase} | {…} | {…} |

> _{Helper question, if it fits — e.g. "Are the changes bulk (a whole category) or individual (specific line items)?"}_

### Drilling into details / other screens

{How many times during the process did the interviewee drill into details? What did they look at? Was there a pattern (routine) or was it checking problematic cases?}

### Searching / adding outside the default view

{Did they use search? How — by name, code, category? Which items did they add outside the system's recommendations?}

### Using other sources during the process

{Did they open other systems / documents / apps along the way? Which ones and why? This is a key signal: every step "outside the system" is a potential functional gap.}

### Interviewee's verbal comments

{Short quotes said spontaneously during the observation. No interpretation.}

- "{verbatim quote}"
- "{verbatim quote}"
- "{verbatim quote}"

### Emotions and attitude

{1–3 sentences — how they behaved. Calm and routine? Frustrated? Enthusiastic? Were there visible moments of hesitation, checking, stopping?}

### Personal rules and workarounds

_{A very valuable section — what the interviewee worked out on their own because the system doesn't support them. Each such rule = a signal of a missing feature or of the default flow not fitting.}_

- {Rule / workaround 1 — e.g. "Always approves van stock corrections right before ordering parts"}
- {Rule / workaround 2 — e.g. "Goes through the form from the bottom up"}
- {Rule / workaround 3}

---

## 4. Follow-up questions after the observation

_{After the observation the researcher asks about specific topics — following the interview script. Each subtopic as a subheading with a question → answer table or a short paragraph.}_

### {Subtopic 1 — e.g. System recommendations and forecasting}

| Question | Answer |
|---------|-----------|
| {question} | {answer — briefly, with a quote if helpful} |
| {question} | {…} |
| {question} | {…} |

### {Subtopic 2 — e.g. Form / attributes}

| Question | Answer |
|---------|-----------|
| {question} | {…} |
| {question} | {…} |

### {Subtopic N — e.g. Other categories / threads not covered in the observation}

{…}

---

## 5. Hypothesis: {specific — e.g. missing parts in the van / excess orders / etc.}

_{Optional — fill in if the visit script included testing a specific research hypothesis.}_

> _{Helper question — e.g. "Does X happen? What could help?"}_

{The interviewee's answer + the researcher's observations confirming or refuting the hypothesis.}

---

## 6. Closing question

> _Is there anything else we should pay attention to?_

{Anything the interviewee wanted to add outside the script. Sometimes this is where the strongest insight shows up.}

---

## 7. Key observations and conclusions

_{The most important insights from the visit — to use in design. This is **the researcher's interpretation**, so here you can draw conclusions (separately from the raw observation above). Each point: a title + a paragraph with evidence (a quote / a specific observation).}_

1. **{Insight 1 — short title, e.g. "the technician trusts the suggested work-order sequence as a starting point, but has a clear correction logic of their own"}**
   {3–5 sentences of elaboration. What exactly was observed, which quote confirms it, what it means for design.}

2. **{Insight 2}**
   {…}

3. **{Insight 3}**
   {…}

4. **{Insight N}**
   {…}

---

## 8. Loose thoughts

_{Uncategorised observations, intuitions and hypotheses of the researcher. Material for further validation in later visits or through analytics. Not formal conclusions — the researcher's raw stream of thought after the visit.}_

- {Thought 1}
- {Thought 2}
- {Thought 3}

---

## Attachments / sources

- {Recording / photos from the visit — if any}: [{file name}]({link})
- Transcript (if any): [{file name}]({link})
- Related visits / interviews:
  - [{another visit from the same initiative / segment}]({link})
  - [{remote interview with the same persona}]({link})
- Related documents:
  - [{user story / brief / epic}]({link})
  - [{visit index — the folder's README}]({link})

---

*Report generated by {skill / author} | {YYYY-MM-DD generated}*
