# Template: DevOps User Story

> **How to use:**
> - A DevOps User Story describes value delivered to development or operations teams — not to the app's end user
> - The DevOps team does not use the Discovery process — the US goes straight to the PRJ4 backlog
> - A US in Jira **Delivery (PRJ4)** with `label: PRJ4`, `Sprint: PRJ4 BACKLOG`
> - Every US should be linked to a specific epic
> - Acceptance criteria = scenarios to verify (not requirements — those go in the Requirements section)
> - Link the US in Jira to its related tickets — dependencies, blockers, delivery tasks
> - Write documentation in plain English; standard industry terms are fine (pipeline, deploy, cluster, node, Helm, Terraform)
> - A US cannot go to refinement with open questions — every question must be closed or explicitly moved to Out of scope

---

## 📋 Header

| Field | Value |
|------|---------|
| **Epic** | {link to the Epic} |
| **UX Brief** | {link to the UX Brief in Confluence — optional} |
| **Figma** | {link to the mockup — optional} |
| **Related US** | {links to related User Stories, if any} |
| **Dependencies** | {links to blocking or blocked tickets} |
| **Change type** | { Project work } |
| **Affects** | _(product or area name)_ |
| **Work type** | { Development } |

---

## User Story

> As a **{role: DevOps / Platform Engineer / SRE}** I want **{action / capability}**, so that **{goal / value — e.g. faster deploys, better observability, higher availability}**.

---

## Context and goal

_{A short description — why this story exists, what problem it solves, what happens without this feature. 2–4 sentences. May refer to an operational process, an SLA or an incident.}_

---

## Functional requirements

_{The detailed logic — what the system/infrastructure has to do. Configuration, pipeline behaviour, policies, alerts.}_

### 1. {Requirement area name}

1.1. {Requirement}

1.2. {Requirement}

### 2. {Requirement area name}

2.1. {Requirement}

_(Numbered sections — every requirement has a unique ID such as 1.1, 2.3 — which makes them easy to refer to in reviews and comments)_

---

## Technical criteria

_{Non-functional requirements — performance, security, availability, compliance.}_

- {e.g. The solution must be compatible with Kubernetes 1.28+}
- {e.g. Secrets must not be stored in the repository — Vault/Sealed Secrets is required}
- {e.g. Failover switch time < 30s}
- {e.g. The change must be reversible without downtime (blue-green / canary)}

---

## Acceptance criteria

_{Testable scenarios that verify the story is finished. Each criterion should give a clear YES/NO answer.}_

> **Tips:**
> - ACs should be verifiable without looking at the infrastructure code
> - Cover these scenarios: happy path, failure/rollback, edge case
> - If an AC concerns a metric — give the threshold and the measurement period

- [ ] **AC1:** {e.g. The pipeline deploys to staging in < 5 min}
- [ ] **AC2:** {e.g. If the deploy fails, it automatically rolls back to the previous version}
- [ ] **AC3:** {e.g. A Slack alert appears within < 1 min of CPU crossing the 80% threshold}

---

## Out of scope

_{What is explicitly excluded from this story.}_

- {Item X — handled in {link to ticket}}
- {Item Y — outside the scope of this iteration, decision: {reason}}

---

## Open questions

| # | Question | Owner | Status |
|---|---------|------------|--------|
| 1 | {question} | {name / role} | ⏳ Open |
| 2 | {question} | {name / role} | ✅ Closed: {answer} |

---

## Notes / Decisions

_{Optional — agreements from refinement, architecture decisions, context that fits no other section.}_
