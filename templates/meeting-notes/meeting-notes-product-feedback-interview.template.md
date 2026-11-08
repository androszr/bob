---
title: "Product feedback interview: {Name / Role}"
type: meeting
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — what the meeting was about and its main takeaway}"
---

# Template: Product feedback interview for an existing product (Meeting Notes)

> **What this template is for (read carefully before you choose it):**
> - A conversation with a user of an **existing product / app that is in use** — we ask "how do you use X, what works, what hurts, what would you improve".
> - Output: **a map of real usage** + a list of praise + a list of pain points + improvement suggestions (grouped by theme + priority).
> - Goal: input for the product roadmap, prioritising pain points, identifying under-used modules.
>
> **When NOT to use:**
> - if it was a **discovery interview about the problem** (the product doesn't exist yet, we are researching the need) → use `meeting-notes-discovery-interview.template.md`
> - if it was an **observation at the workplace** (contextual inquiry / shadowing) → use `meeting-notes-contextual-inquiry.template.md`
> - if it was an AS IS → TO BE process-mapping workshop with several participants → use `meeting-notes-as-is-to-be.template.md`
> - if the goal was to settle a decision → use `meeting-notes-decision.template.md`
> - if it was a status update / sync → use `meeting-notes-status-update.template.md`
>
> **How to use:**
> - **The structure follows the product, not the chronology** — TOP used features, what works, what doesn't, suggestions grouped by theme (per module). The interviewee naturally thinks "in the context of feature X", not "in the order I said things".
> - **Always keep verbatim quotes** for the stronger moments (praise, pain points, suggestions). No paraphrasing inside a quote.
> - **Group the suggestions section by module / product area** — this makes it easier to turn into a backlog later. Within a module, order by priority.
> - **Explicit priorities** — 🔴 critical, 🟠 medium, 🟢 nice-to-have. Without them the whole document looks like "all P1".
> - Keep **praise** ("what works well") apart from suggestions — it matters to the product team, so they don't break what already works.
> - **Convert relative dates to absolute ones** (e.g. "in January" → "January 2026").
> - YAML frontmatter per the folder's convention (`type: interview`, app, platform, role, tags, summary).
> - Name the file: `{YYYY-MM-DD}-{role}-{first-last-kebab}-interview.md`.

---

```yaml
---
type: interview
initiative: {initiative-name-kebab}
date: {YYYY-MM-DD}
role: {technician / dispatcher / customer / po / expert / other}
interviewee: {First Last}
app: {product-name-kebab}
platform: {ios / android / web / desktop / mobile+desktop / n/a}
source: {transcript / notes / recording}
tags: [{module1}, {module2}, {topic1}, {ux}]
summary: "{2–4 sentences — who, the usage context, the strongest points (what works), the biggest pain points, the main suggestions}"
---
```

# Interview — {Role} {First Last} ({YYYY-MM-DD})

## Interviewee context

_{3–6 sentences. What is their context for using the product? For how long? Which device / platform dominates? What is specific about their work / branch / organisation that affects how they use the product? This is the foundation — without it the suggestions hang in a vacuum.}_

- {Context point 1 — e.g. "Regional manager with one branch + also coordinates subcontractors"}
- {Context point 2 — e.g. "Mostly uses the app on their phone — uses the tablet less often"}
- {Context point 3 — e.g. traffic patterns, working hours, team structure, tools outside the product}

---

## TOP — what they use most often

_{The most important section. What does the interviewee **actually** click every day / several times a day? Give 3–5 main ones + a list of other frequent ones. A key quote next to the strongest feature, if there was one.}_

### Top 3 daily

1. **{Feature 1}** — {usage context, frequency, why this one is #1}. "{quote if helpful}"
2. **{Feature 2}** — {context, frequency}
3. **{Feature 3}** — {context}

### Also used very often (daily / several times a day)

- **{Feature 4}** — {context}
- **{Feature 5}** — {context}
- **{Feature N}** — {context}

---

## What they use less often / don't use at all

_{The second most important section — features that exist but the interviewee doesn't use. Always ask **why**. No adoption = either poor visibility, a feature with no value, or a poor implementation.}_

- **{Feature A}** — {why they don't use it, quote if there was one: "{quote}"}
- **{Feature B}** — {why}
- **{Feature C}** — {why}
- **{Feature N}** — {why}

---

## What works well (praise)

_{Collect praise deliberately — it's easy to focus only on pain points and miss what already works. This is key information for the team ("don't touch this, it works"). Each piece of praise: brief + a quote.}_

- **{Element / feature 1}** — "{verbatim quote of praise}"
- **{Element / feature 2}** — "{quote}" / {description of a successful change that was shipped, who was responsible for it}
- **{Element / feature 3}** — {what exactly is good}
- **Overall:** "{quote of an overall positive assessment, if there was one}"

---

## Improvement suggestions (grouped by theme)

_{The largest section — suggestions grouped **per module / product area**, because that is how the interviewee thinks about them. Within a module, most important first. Each point: a short description + the reason + an estimate of the impact (if it came up in the conversation).}_

### {Module 1 — e.g. Dashboard / home page}

- **{Suggestion 1}** — {description, what exactly is missing, what effect it would have. A quote if helpful.}
- **{Suggestion 2}** — {description}
- **{Suggestion 3}** — {description}

### {Module 2 — e.g. Orders}

- **{Suggestion 1}** — {description}
- **{Suggestion 2}** — {description}

### {Module 3 — e.g. Notifications}

- **{Suggestion 1}** — {description}
- **{Suggestion 2}** — {description}

### {Module N}

- **{Suggestion}** — {description}

### Other / general gaps

_{Cross-cutting suggestions that don't fit in a single module. E.g. about navigation, overall design, general notification hygiene, missing system-level features.}_

- **{Suggestion 1}** — {description}
- **{Suggestion 2}** — {description}

---

## Priorities (from the interviewee's or the researcher's perspective)

_{A helpful summary section — which suggestions are "this would change my life today" and which are "it would be nice, but I'll survive". It isn't always obvious from the previous section; here it is spelled out.}_

### 🔴 Critical / high

1. **{Suggestion}** — {1 sentence on why it is critical}
2. **{Suggestion}** — {…}
3. **{Suggestion}** — {…}

### 🟠 Medium

4. **{Suggestion}** — {…}
5. **{Suggestion}** — {…}

### 🟢 Nice-to-have

6. **{Suggestion}** — {…}
7. **{Suggestion}** — {…}

---

## Quotes worth remembering

_{The best-of quotes — the 4–8 strongest ones that capture the tone of the conversation. They help you come back to the document later and recall the context without reading the whole thing. Each quote gets a short label (what it is about).}_

- On the whole: *"{overall assessment quote}"*
- On {module / feature A}: *"{quote}"*
- On {module / feature B}: *"{quote}"*
- On {pain point}: *"{quote}"*
- On {suggestion}: *"{quote}"*

---

## Usage pattern (compressed into one paragraph)

_{Optional — a very valuable one-paragraph summary (4–6 sentences). How does this type of user think about the product? What is core for them and what is peripheral? This section is the one most often quoted in briefs and initiative summaries.}_

{4–6 sentences of compression. Write it so that someone who hasn't read the rest of the document understands the interviewee's perspective and has a direction in which it is worth developing the product for this user segment.}

---

## Attachments / sources

- Audio transcript: [{file name}]({link})
- Key moments in the recording (if there is audio):
  - {MM:SS} — {topic / key quote}
  - {MM:SS} — {"aha" moment}
  - {MM:SS} — {praise / pain point #N}
- Related interviews / documents:
  - [{another interview with the same persona / initiative}]({link})
  - [{user story / brief / epic}]({link})
  - [{interview index — the folder's README}]({link})
