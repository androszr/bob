---
title: "{Short User Story title}"
type: user-story
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — as whom, what, why}"
---

# Template: User Story

> **How to use:**
> - Fill in the header and sections roughly before Discovery starts, then expand them in each phase
> - Check it against the Definition of Ready before moving it to Delivery as Ready for Planning
> - Acceptance criteria = scenarios to verify (not functional requirements — those go in the Requirements section)
> - ⚠️ The three rules below apply to the **Acme — mobile app (PRJ1) and portal (PRJ2)** projects. Other projects may use different queue names and conventions.
> - A US in Jira **Discovery (PRJ3)** = a US created and developed during Discovery
> - A US in Jira **Delivery (PRJ1)** = a US handed to the team for delivery
> - Only a US from the PRJ1 queue can enter a sprint — after PO acceptance and once it meets the Definition of Ready
> - Every US should be linked to a specific epic
> - Link the US in Jira to its related tickets — dependencies, blockers, delivery tasks
> - Write documentation in plain English; standard industry terms are fine (User Story, refinement, sprint, discovery)
> - A US cannot go to refinement with open questions — every question must be closed or explicitly moved to Out of scope

---

## 📋 Header

| Field | Value |
|------|---------|
| **Epic** | {link to the Epic} |
| **UX Brief** | {link to the UX Brief in Confluence} |
| **Figma** | {link to the mockup — a specific node} |
| **Related US** | {links to related User Stories, if any} |
| **Dependencies** | {links to blocking or blocked tickets} |
| **Label** | {e.g. orders / schedule / map / offline / notifications / design} |
| **Change type** | {e.g. Project work} |
| **Affects** | {e.g. Mobile app / Portal / Platform} |
| **Work type** | {e.g. Development / Business analysis — TO-BE state} |

---

## User Story

> As a **{user role}** I want **{action / capability}**, so that **{goal / value for the user}**.

---

## Context and goal

_{A short description — why this story exists, what problem it solves, what happens without this feature. 2–4 sentences. May refer to a business process or user journey.}_

---

## Functional requirements

_{The detailed logic of how the system behaves. These are not acceptance criteria — they describe what the system has to do. May include field mappings, screen states, error handling, edge cases.}_

### 1. {Requirement area name}

1.1. {Requirement}

1.2. {Requirement}

### 2. {Requirement area name}

2.1. {Requirement}

_(Numbered sections — every requirement has a unique ID such as 1.1, 2.3 — which makes them easy to refer to in QA and in comments)_

---

## Acceptance criteria

_{Testable scenarios that verify the story is finished from the user's point of view. Each criterion should give a clear YES/NO answer. They do not describe how the system works inside — they describe what the user sees and can do.}_

> **Tips:**
> - Add 1–2 sentences describing the scope of this section — what the ACs cover (e.g. "The ACs cover the main path for closing a service order and handling missing parts")
> - If a criterion refers to a specific view — add a link to the Figma node next to that AC
> - Put explicitly excluded items in the Out of scope section, with the reason

- [ ] **AC1:** {The user can / The system shows / When X, then Y}
- [ ] **AC2:** {Error scenario — when X, the system shows Y}
- [ ] **AC3:** {Edge case — when X is empty/null/invalid, then...}

_(Tip: every AC should answer the question "can a tester verify this without looking at the code?")_

---

## Initial estimate

- Analysis / architecture: —
- BE: —
- Web: —
- Android: —
- iOS: —
- Design: —
- Testing: —
- PM / DevOps: —
- **TOTAL: —**

**Role Identity:**
- [ ] Service technician
- [ ] Dispatcher
- [ ] Regional manager
- [ ] Customer
- [ ] Admin

Notes: —

---

## Out of scope

_{What is explicitly excluded from this story. Especially important when part of a feature is "visible but inactive" or "handled in a separate requirement".}_

- {Item X — handled in {link to ticket}}
- {Item Y — outside the scope of this iteration, decision: {reason}}

---

## Open questions

| # | Question | Owner | Status |
|---|---------|------------|--------|
| 1 | {question} | {name / role} | ⏳ Open |
| 2 | {question} | {name / role} | ✅ Closed: {answer} |

---

## Notes / Design decisions

_{Optional — agreements from refinement, decisions that matter in hindsight, context that fits no other section.}_

---

## Technical criteria (optional)

_{Additional technical requirements — API error handling, performance limits, security. Filled in by the SA or BA after talking to the development team.}_

- {e.g. The endpoint must respond in < 500ms at p95}
- {e.g. On an HTTP 5xx error — the system shows message X and logs the event}
- {e.g. Sensitive data is not logged on the client side}
