# Prioritize the backlog

Use this when several product outcomes compete for delivery or the user asks to rank, reorder, or groom existing work. A request to prioritize authorizes updating the order of the requested backlogs and tickets. It does not authorize scope changes or implementation.

## Decide as Product Owner

1. **Inspect active work.** Read the task index, active epic READMEs, and relevant tickets. Verify overlaps and dependencies against the code and finished work as needed. Keep completed work and its IDs available as evidence.
2. **Group by customer outcome.** Give independently valuable outcomes separate epic backlogs when they can be reviewed and delivered independently. Keep closely related stories together and link shared dependencies. A single feedback document can contain several outcomes. Fold genuine duplicates into existing work.
3. **Rank the work.** Weigh observed customer impact, frequency and severity of failures, value unlocked, dependencies, estimated effort, and delivery uncertainty. Within each epic, order its tickets by prerequisites and customer value. Preserve explicit sequencing commitments. For example, if the customer asked to review a clickable prototype before implementation, rank the prototype review ahead of the production work it represents.
4. **Separate investigation from delivery.** If feasibility could change the roadmap, time-box a spike. It may happen early or in parallel while implementation stays later. Explain what the result must establish before delivery work moves forward.
5. **Record the decision.** Name the first task, the later work, and the reasoning for each material trade-off. Prioritize with the information available. Ask the customer one short question only if an unresolved business goal or constraint could materially change the order. Keep the rest of the prioritization moving.

Ordering is a PO decision. Requirements confirmation covers user-facing behavior and business constraints. A change in priority alone does not reopen confirmed requirements. An open requirement can leave a ticket pending review while the backlog still has a clear proposed delivery order.

## Apply the order in the repository

- Update the active portfolio order in `docs/tasks/README.md` for multi-epic work. Include the rank, backlog link, and brief rationale. Update each affected epic README so the ticket rows reflect execution order.
- If the user asks for the task folders themselves to reflect delivery order and the repository uses numbered folder names for that purpose, reorder the active folder prefixes. Move matching PRD folders when the repository pairs those paths. Update affected relative links, including links from other epics or plans. Only move folders in scope.
- Preserve ticket IDs, ticket filenames, acceptance criteria, and statuses when the request is only to reorder. Recheck that prerequisites precede their dependents and that affected links resolve. Call out work that can run in parallel or is deliberately deferred.
- Keep the reply short. Link the updated index and first task, and state the significant ordering decisions.
