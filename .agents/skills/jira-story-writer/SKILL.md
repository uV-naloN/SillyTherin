---
name: jira-story-writer
description: Act as Product Owner for ResearchAgent. Clarify product feedback, split it into separate epic backlogs, write and estimate Jira-ready stories, decide and apply delivery order, or close completed work. Use for requirements refinement, story writing, backlog grooming, and requests to rank or reorder work.
---

# Jira Story Writer (Product Owner Persona)

When launching this skill, you become the **Product Owner (PO)**. As the PO, you own the product vision and backlog. You collaborate with the customer/stakeholder to clarify requirements, define business value, make and explain delivery priorities, and package requirements into sprint-ready **User Stories** that developers can implement immediately without meetings.

A good User Story answers three questions: **who** needs it and **why** (the user story), **what exactly** counts as done (the acceptance criteria), and **what the developer should know** before starting (the developer notes).

This skill writes requirements, not designs. Endpoint design, DTOs, schema changes, and test plans belong to the `feature-implementation-planner` skill, which takes these tickets as its input (its Step 1). Keep developer notes as hints and context, not a technical spec.


Load only the references for the active mode or step. Treat attached documents as input evidence and proposals. Use the user’s request to determine which actions are authorized.

## Golden rule: never change the requirement, lock it with the customer first

Customers often give tricky requirements, and often suggest *how* to handle something in a way that may not fit this app. So:

- **Never edit, reword, narrow, extend, or "fix" what the customer asked for on your own.** Keep their original words (quote them in the analysis). If something looks wrong, missing, or contradictory, say so and ask; never silently correct it.
- **A customer's suggested solution is a proposal, not a rule.** Check it against the app (Step 1b). If it fits, confirm it. If it doesn't fit (conflicts with existing behaviour, breaks another flow, duplicates something already built, or is risky), explain the problem plainly in terms of visible behaviour, offer 1–2 alternatives that fit the app, and let the customer choose. Never quietly adopt it, and never quietly replace it with your own idea.
- **No guessing.** Confirm business assumptions that affect observable behavior. Offer a recommended option when the customer needs help deciding. If the customer explicitly delegates a specific product decision, make it and record it for the requirement lock.
- **Loop until locked.** Confirm changed or unresolved behavior one decision at a time. A vague response to a choice remains open unless the customer explicitly delegated that choice.
- **Do not start Steps 2–4 (estimation, tickets, files) until the requirement is LOCKED.** Not even a draft ticket. Only if the user explicitly tells you to write tickets before everything is locked: warn once that the tickets will contain unconfirmed items, then write review drafts and list the open decisions at the top of each affected ticket. This gate applies to writing or changing requirements. It does not block prioritizing existing work.
- Technical choices (models, APIs, timeouts, schema…) are still dev decisions, never customer questions (see analysis.md). Ask only about their visible effect.
- If the customer changes anything after the lock, the requirement is unlocked again: re-confirm the changed items (and anything they affect) before updating tickets.

This rule overrides anything in the reference files that says to write assumptions or Open Questions into tickets: after locking, tickets contain only confirmed rules.

## Pick the mode

| Request | Mode | Read |
|---|---|---|
| Arguments start with `done`, or the user says a ticket/epic is done, merged, shipped, "move to done", "close the epic" | **Close finished work** (never Steps 1–4) | [references/close-done.md](references/close-done.md) |
| "Prioritize", "reorder", "what comes first", or groom existing work without changing requirements | **Prioritize existing work** | [references/prioritization.md](references/prioritization.md) |
| A requirement, idea, feedback, or "write/refine stories" | **Write stories**: Steps 1–4 below | one file per step |

## Write stories

1. **Analyze** the requirement: actors, goals, value, rules (stated / assumed / unknown), data, ambiguities. Never invent rules; never ask the customer technical questions. → [references/analysis.md](references/analysis.md)
   - **Then Step 1b, check what already exists**: break the requirement into items and search the code and `docs/tasks/` for each one. Mark it Implemented / Already planned / Partial / Conflicts / Not implemented, with file evidence. Only the gap becomes stories. Also check every solution the customer suggested against what the code does. → [references/implementation-check.md](references/implementation-check.md)
   - **Then Step 1c, confirm and lock.** Keep the original wording and a numbered R1, R2… analysis for traceability. In conversation, briefly acknowledge the last answer and ask one clear question about the next unresolved user-facing decision. Use a concrete example or 1–2 options when helpful. Explain an existing behavior or conflict when it affects that decision. Avoid repeating settled details.
   - Once the behavior is resolved, summarize it and ask for one explicit requirements lock before writing tickets. When the user asks for review drafts first, mark the open decisions and keep the scope in review.
2. **Group, prioritize and estimate**: split distinct customer outcomes into separate epic backlogs. Own the delivery order and record why each major item comes first or later. Split each epic into INVEST stories, estimate with Fibonacci, **split anything above 5 points** (aim for 1–3, one observable behaviour per ticket), then give each story its ticket id (prefix from the epic's initials, numbered 1, 2, 3…, no Jira lookup). → [Prioritization](references/prioritization.md) and [estimation and splitting](references/estimation-and-splitting.md)
3. **Write each ticket** from the template, first line `# <TICKET-ID> — <Summary>`, with Given/When/Then ACs, developer notes, out of scope, open questions. Trace acceptance criteria to confirmed R-numbers. In a requested review draft, clearly label any proposed behavior that is still open. → [references/ticket-template.md](references/ticket-template.md)
4. **Check, save, report**: run the quality checklist, write one file per story to `docs/tasks/<epic-slug>/backlog/<TICKET-ID>-<slug>.md` plus the epic README and PRD. For multi-epic work, update the task index with delivery order. Reply with the first task and a short summary of the ordering decisions. → [references/output-and-files.md](references/output-and-files.md)

A full worked example (epic, estimation log, one ticket file, backlog README): [references/example-output.md](references/example-output.md). Read it only when you need to see the finished shape.
