# Documentation index (optional lookup)

**Coder starts with [AGENTS.md](../AGENTS.md) + approved task only.** Read relevant sections/source/tests, not entire document trees. This is a router, not extra onboarding.

| Need | Read on demand |
| --- | --- |
| Code conventions | [Engineering standards](conventions/engineering-standards.md#current-repository-specific-coding-baseline) · [R2 layer/DI](conventions/engineering-standards.md#r2-layer-di-and-file-header-contract) · [validation](conventions/engineering-standards.md#r2-validation-and-helper-ownership) |
| Database | [ORM/DTO](conventions/engineering-standards.md#postgresql-orm-and-dto-conventions) · [three access patterns](conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership) · [runtime factory](guides/postgres-connection.md) · [Alembic](guides/postgres-alembic.md) · [disposable PG16](guides/postgres-development.md) |
| Current source / R2 target | [Architecture](architecture.md) · [import policy](../architecture/policy.toml) · [blueprint](architecture/r2-clean-architecture-blueprint.md) · [five-gate target](architecture/r2-quality-gates-and-tests.md) |
| Git / task delivery | [Worktrees](guides/local-worktrees.md) · [scope template](templates/agent-task-contract.md) · [PR handoff](templates/handoff.md) |
| TL only | [Operating procedure](guides/techlead-operating-procedure.md) · [review checklist](templates/tl-review-checklist.md) |
| Runtime / security (feature-specific only) | [V1 product gates](v1-scope-and-gates.md) · [runtime plugins](architecture/worker-runtime-plugins.md) · [provider HTTPS](guides/provider-https-transport.md) · [OAuth](auth0-oauth.md) · [TL integration](guides/mcp-techlead-integrations.md) · [dispatch recovery](guides/dispatch-recovery.md) · [runtime inventory](../.runtime/README.md) |
| History only | [CL-12 ADR](adr/CL-12-architecture.md), [PG02-B](architecture/pg02-b-workflow-adapter-contract.md), [Phase 3.1](phase-3.1.md), [my-wfc](my-wfc.md), [handoffs](history/handoff-log.md), docs/tasks, docs/evidence, old POC/SQLite runbooks. Do not execute old commands or infer current approvals/deployment. |

Current merged source/test evidence wins over outdated descriptions of implementation. Task and AGENTS control write authorization. Report conflicts to TL; no duplicate status snapshots or convention text here.
