# PR review checklist

Use the PR's actual base and diff. For a local feature branch, compare against `origin/main` by default. Lead with actionable findings in severity order and cite the affected file and line. Write feedback for the user and post it only if requested.

- Check for behavioral regressions, breaking API or stored-data changes, authorization and ownership failures, credential exposure, and missing error handling.
- For Alembic revisions, check the parent revision, competing heads, forward compatibility, and upgrade behavior with existing data. Review rollback or recovery for destructive changes.
- Check for SQLAlchemy queries in loops, excessive row loading, and missing indexes on new joins or filters.
- Check that business rules, database access, and presentation remain separate. Review type hints, domain names, tests, and any affected frontend build output.
- Report verification gaps and avoid marking unrun checks as passed.
