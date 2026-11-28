# PR body template

Fill in the applicable entries from the diff. Include the migration name and data impact for database changes.

```markdown
## 1. Ticket
- [<JIRA-ID>](<Jira link>) — <story title>

## 2. Summary of Changes
- **Objective** — <problem this PR solves>
- **Key Changes**
  - <user-visible behavior, API or command changes>
  - <database schema or migration update and rollout implications>
  - <service, frontend, or infrastructure changes>

## 3. Technical Checklist
- [ ] Relevant Python or frontend checks pass.
- [ ] Changed database queries were checked for N+1 behavior and missing indexes.
- [ ] Alembic migration upgrade was verified, if applicable.

## 4. Proof
<test results and any relevant UI evidence>
```
