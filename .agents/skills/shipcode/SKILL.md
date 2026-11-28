---
name: shipcode
description: Create or name a branch, write a commit, sync a feature branch, prepare or open a pull request, or review a PR or diff for ResearchAgent. Covers Jira branch names, conventional commits, the PR template, verification, and Alembic migration review.
---

# Ship code

Follow only the steps the user requested. For a review, use [the review checklist](references/review-checklist.md) and provide findings with file and line references. Post feedback only when the user asks.

1. **Ticket and branch**. Find the Jira key in the request, branch name, story, or plan. Use `<type>/<JIRA-ID>-<short-description>` for a new ticket branch, for example `feat/FR-6-trace-reply-latency`. Keep an existing feature branch unless the request calls for a new one. Use the actual Jira link when available.
2. **Commit**. Check status and the diff. Stage the files that belong to the requested change. Use `<type>(<scope>): <imperative description>` for the subject, with a lowercase description and no trailing period. Omit assistant attribution, co-author trailers, and session URLs unless explicitly requested.
3. **Sync**. When the user requests a sync or full ship, fetch `origin` and check the PR base. The default here is `main`. Rebase your feature branch onto `origin/main` where appropriate. Resolve clear conflicts and ask about conflicting behavior when the intended result is ambiguous. Coordinate before rewriting a shared branch.
4. **Verify**. For Python changes, run `.venv/bin/python -m pytest -m "not docker"` and `.venv/bin/python -m ruff check sillytherin tests`. For frontend changes, run `npm test` and `npm run build` from `frontend/`. Review SQLAlchemy queries for N+1 patterns and indexes on new joins and filters. Address failures introduced by the diff and record the actual test results in the PR.
5. **Migrations**. For changes under `sillytherin/db/migrations/versions/`, use `.venv/bin/python -m alembic heads` to check the revision graph. If a rebase leaves competing heads, inspect both revisions to decide how to reconcile them without changing an already-applied revision. Test upgrade on a disposable database. Describe migration risks, compatibility, and any data changes in the PR. Draft a team announcement when needed and send it only if authorized.
6. **Push and PR**. Push the requested feature branch. Use `--force-with-lease` only with authorization for the user's feature branch after a rebase. Update the existing PR when one exists. Otherwise open against the confirmed base with [the PR template](references/pr-template.md). Use the commit convention for the PR title and check only items you verified.
7. **Report**. Include the completed steps, commit, test results, and PR link as applicable.
