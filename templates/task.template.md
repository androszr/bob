---
title: "{Short task title}"
type: task
date: {YYYY-MM-DD}
author: {First Last}
summary: "{1 sentence — what needs doing and why}"
---

# Template: Task

> **How to use:**
> - A task is a smaller piece of work the Team takes on for Discovery or Delivery.
> - ⚠️ The three rules below apply to the **Acme — mobile app (PRJ1) and portal (PRJ2)** projects. Other projects may use different queue names and conventions.
> - A task in Jira **Discovery (PRJ3)** = a task taken on for Discovery
> - A task in Jira **Delivery (PRJ1)** = a team task to be delivered
> - Only a task from the PRJ1 queue can enter a sprint
> - Both kinds of task should be estimated in story points during refinement
> - A task for the PRJ4 team should carry: `label: PRJ4`, `Sprint: PRJ4 BACKLOG`
> - Every task should be linked to a specific epic
> - Fill in the header and sections roughly before you start, then expand them in later phases
> - Check it against the Definition of Ready before moving it to Delivery as Ready for Planning

> **When to use:**
> - when the work does not need the User Story form — it does not have to deliver value to the end user on its own
> - a smaller piece of functionality or work that supports a US or an epic
> - a concrete activity: technical (BE/FE/DB), analysis, QA, DevOps, design

---

## 📋 Header

| Field | Value |
|------|---------|
| **Epic** | {link to the Epic} |
| **Task type** | {Implementation / Analysis / DevOps / Design / QA} |
| **Related User Story** | {link to the US this task comes from} |
| **Component / Layer** | {e.g. BE / FE / DB / Infrastructure} |
| **Label** | {e.g. orders / schedule / map / offline / notifications / design / PRJ4} |
| **Change type** | {e.g. Project work} |
| **Affects** | {e.g. Mobile app / Portal / Platform} |
| **Work type** | {e.g. Development / Business analysis — TO-BE state} |

---

## Task goal

_{One sentence: what and why. Where this task comes from — a reference to the feature or problem in the US.}_

> {e.g. "Align the Orders microservice data model with the current event contract, so that US PRJ1-XXXX works correctly."}

---

## Scope of work

_{A numbered list of concrete things to do. Detailed enough to estimate and to track progress.}_

### 1. {Work area}

- {Concrete change / action}
- {Concrete change / action}

### 2. {Work area}

- {Concrete change / action}

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

## Dependencies and risks

_{Optional — what could block the work, what has to be ready before it starts.}_

- {Dependency / risk}
