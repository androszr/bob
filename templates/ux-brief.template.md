---
# File name convention:
#   {YYYY-MM-DD}-ux-brief-{initiative-slug}.md
#
# - The date in the name = `updated:` from the frontmatter (last substantive update)
# - On every significant update: change `updated:` and rename the file, so the dates match
# - The creation date lives in `date:` and in the Metadata table
title: "UX Brief: {Initiative name}"
type: ux-brief
initiative: {initiative-slug}
area: {mobile | portal}
status: draft
date: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
author: {First Last of the PO/PD}
epic: {link to the Epic in Jira, or an empty string}
ticket: {link to the US / Task, or an empty string}
tags: [{tag1}, {tag2}]
summary: "{1 sentence — what the initiative is about and who it is for}"
---


# UX Brief: {Initiative name}

## Metadata

| Field | Value |
|------|---------|
| Project | {project name} |
| Product / Module | {product name} |
| Epic (Jira) | {link to the Epic — the "Epic Link" field in Jira} |
| Ticket (Jira) | {link to the US / Task} |
| Status | `draft` |
| Author (PO/PD) | {First Last} |
| Created | {YYYY-MM-DD} |
| Last updated | {YYYY-MM-DD} |
| Confluence | _(add the page link once published)_ |
| Miro | _(link to the Miro board / frame, if any)_ |

## Introduction

> {2–3 sentences: what the initiative is about, what the key problem is, who it is for. It orients someone who lands on this brief for the first time — without having to read the whole thing.}

---

> **How to use this document:**
> - 🟢 **Gate 1 (PO + BO)** - the PO fills it in roughly before the kick-off meeting; it is discussed at Gate 1
> - 🔵 **Problem Space (BA, PD)** - the BA and PD deepen and fill in the Gate 1 sections + add new ones
> - 🟣 **Solution Space (BA, PD, SA)** - verify and finalise the differentiators based on the solutions worked out
> - Sections 1–6 live throughout discovery - **Gate 1 is the starting point, not the final word**

---

# 🟢 GATE 1 - Discovery start (PO + BO)

_The PO fills this in roughly, based on what was agreed with the BO, **before the kick-off meeting**. Discussed at Gate 1 with the BA, PD and SA. The BA and PD will deepen and fill in these sections in Problem Space._

## 1. Users

**Who are we doing this for?**
_(Who uses this screen/flow/product? Link to the persona)_

> {description of the users}

### User Story

> As a **{who}** I want **{what}**, so that **{why / value}**.

---

## 2. Goal and context

**What is the goal of the task/project?**
_(PM/PO/Business: where the change comes from, what we want to achieve)_

> {goal}

**Are there factors outside the product that affect this project?**

> {dependencies, external context, links}

**How does the task/project relate to the product strategy?**
_(Wider context, business strategy)_

> {relation to the product strategy}

**How does the task/project relate to the organisation's strategy?**
_(Which value stream it supports, and how)_

> {relation to the organisation's strategy}

**What do we lose if we don't make the change?**
_(e.g. users won't complete their tasks, we will repeat competitors' mistakes, we won't meet users' expectations)_

> {consequences of doing nothing}

**How will the project/task improve the user experience or save the user time?**

> {expected UX improvement}

**Have you tried making changes like this before?**

> {history of attempts, lessons learned}

---

## 3. User problem and needs - initial

_The PO describes the problem at a general level, from the BO's point of view and from reported issues. The BA fills in the details in Problem Space._

**What is the user's main problem?**
_(Have users reported anything before? Customer service data, insights)_

> {problem description - initial, from the PO/BO point of view}

**How does the user cope with this problem today?**
_(Do they have their own workarounds, ways to make the work easier?)_

> {description of the workaround}

---

## 4. Stakeholders (RACI)

**Who is responsible for the initiative?**
_(Who should take part in the kick-off and consultations? Who makes product, design and legal decisions?)_

| Person | Project role | RACI |
|-------|-----------------|------|
| {name} | {role} | Accountable |
| {name} | {role} | Responsible |
| {name} | {role} | Consulted |
| {name} | {role} | Informed |

---

## 5. Differentiators - initial

_Differentiators are the parts of the new system that make service technicians' and dispatchers' work easier and save them time. We measure efficiency in minutes saved per month per technician. The PO fills it in roughly at Gate 1, the BA and PD deepen it in Problem Space, and it is finalised in Solution Space._

**Estimated savings per item:**

| Differentiator (improvement item) | Saving min/month/technician | Analytics data source | Estimate stage |
|---------------------------------|-----------------------------|----------------------------|-------------|
| {item name} | {X min} | {e.g. analytics, research, observation} | 🟢 Gate 1 |
| {item name} | {X min} | {source} | 🟢 Gate 1 |

**Total saving for the initiative:** {total min/month/technician}

> ⚠️ The table is updated iteratively:
> - 🟢 Gate 1 - the PO gives initial estimates based on what was agreed with the BO
> - 🔵 Problem Space - the BA/PD verify and refine them based on the as-is analysis and research
> - 🟣 Solution Space - finalised once the solutions are worked out, verified with the BO

---

## 6. Action plan - initial

_The PO, BA, PD and SA agree an initial schedule at the kick-off meeting._

**Is there an agreed plan/stages of work?**

> {plan / schedule - initial}

**How much time do I have?**

> {deadline, time frame}

**What are the constraints/risks in delivering the plan?**
_(e.g. no time for usability testing, business or technology constraints)_

> {list of constraints}

---
---

# 🔵 PROBLEM SPACE - deepening and filling in (BA, PD)

_After Gate 1 the analysis work begins. The BA and PD **go back to sections 1–6 above** and deepen them with findings from the as-is analysis and research (e.g. refine the User Story, fill in the context, verify the goal, refine the differentiators). Below - extra sections that only come into being at this stage._

## 7. User problem and needs - in depth

_The BA fills this in from the as-is analysis. The PD fills it in after research. It deepens the initial problem description from section 3._

**At which stage of the user journey does the problem occur?**

> {stage / touchpoint}

**What does the current system/view look like?**

> {description of the current state, links to screenshots}

**What are the user's needs?**

> {list of needs - based on analysis and/or research}

**Do users suggest improvements? Which ones?**

> {list of improvement ideas - based on analysis and/or research}

### Current view

_(Screenshots / links to the current state of the UI)_

| Screen | Link / description |
|--------|-------------|
| {name} | {link} |

### Needs (notes)

- {need 1}
- {need 2}
- {need 3}

---

## 8. Data and metrics

_The BA fills this in with data from the as-is analysis. The PD fills it in with research results._

**Is the problem backed by data?**
_(Analytics, quantitative data, business events, customer service / sales reports, comments, desk research and competitors, user recordings, research reports)_

> {available data and sources}

**If we have no data, what are the options for quantitative/qualitative analysis?**

> {proposals for obtaining data}

**What data do we still need? Who will provide it and how long will it take?**

| Data to obtain | Provided by | Due |
|--------------------|---------------|--------|
| {data} | {person/team} | {when} |

**How will we check that our solution works?** _(KPIs + Metrics)_

| KPI / Metric | Current value | Target |
|---------------|----------------|-----|
| {metric} | {baseline} | {target} |

---

## 9. Action plan - updated

_Updated once the scope is set and research is finished. Replaces the initial plan from Gate 1._

**At what point will we test?**

> {planned validation point}

---
---

# 🟣 SOLUTION SPACE - verification and finalisation (BA, PD, SA)

_In Solution Space, once the requirements and mockups are worked out, we go back to the differentiators (section 5) and finalise them._

## 10. Differentiators - final

_After the PO and BO accept the requirements and mockups - the final differentiators table with verified data._

| Differentiator (improvement item) | Saving min/month/technician | Data source | Verification status |
|---------------------------------|-----------------------------|---------------|--------------------|
| {item name} | {X min} | {source} | ✅ Verified / ⏳ To verify |

**Total saving for the initiative:** {total min/month/technician}

**Changes vs Gate 1 / Problem Space:**

> {what changed in the estimates and why}
