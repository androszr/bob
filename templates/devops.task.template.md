# Template: DevOps Task

> **How to use:**
> - A DevOps task is a concrete unit of infrastructure or operations work
> - The DevOps team does not use the Discovery process — tasks go straight to the backlog
> - The task lives in Jira **Delivery (PRJ4)**
> - Every task should be linked to a specific epic
> - The task should be estimated in story points at refinement
> - Fill in the header and sections before refinement
> - Check it against the Definition of Ready before moving it to Ready for Planning

> **When to use:**
> - a concrete infrastructure activity: configuration, migration, automation, monitoring
> - the work does not need the User Story form — it does not deliver value directly to the end user
> - a smaller piece of work that supports a DevOps US or epic
> - an operational task: upgrade, patching, secret rotation, optimisation

---

## 📋 Header

| Field | Value |
|------|---------|
| **Epic** | {link to the Epic} |
| **Task type** | { e.g. Analysis, Task } |
| **Related User Story** | {link to the US this task comes from — if any} |
| **Component / Layer** | _(NONE)_ |
| **Change type** | { Project work } |
| **Affects** | _(product or area name)_ |
| **Work type** | { Development } |

---

## Task goal

_{One sentence: what and why. Where this task comes from — a reference to the problem, incident or requirement in the US/epic.}_

> {e.g. "Set up Prometheus alerts for the Orders microservice to catch performance degradation before it affects users."}

---

## Scope of work

_{A numbered list of concrete things to do. Detailed enough to estimate and to track progress.}_

### 1. {Work area}

- {Concrete change / action}
- {Concrete change / action}

### 2. {Work area}

- {Concrete change / action}

---

## Definition of done

_{The conditions under which the task counts as Done. They should be verifiable.}_

- [ ] {e.g. Alert configured and tested on the staging environment}
- [ ] {e.g. Runbook updated with the new scenario}
- [ ] {e.g. Change deployed to PROD and verified for 24h}

---

## Dependencies and risks

_{Optional — what could block the work, what has to be ready before it starts.}_

- {Dependency / risk}

---

## Rollback plan

_{Optional — how to undo the change if something goes wrong. Required for PROD changes.}_

- {e.g. Revert the Helm release to version X: `helm rollback <release> <revision>`}
- {e.g. Restore the previous configuration from backup: {path/link}}
