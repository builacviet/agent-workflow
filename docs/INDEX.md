# Documentation index (optional lookup)

**Do not read this file by default for a coding task.** Start with [AGENTS.md](../AGENTS.md) and the approved task/AC. This page only helps locate additional reference material when necessary. Read relevant sections and source/tests, not entire document trees.

| If your task concerns… | Read on demand |
| --- | --- |
| Coding quality, Python, security basics | [Engineering standards](conventions/engineering-standards.md#current-repository-specific-coding-baseline) |
| PostgreSQL ORM/DTO or transaction lifecycle | [ORM/DTO](conventions/engineering-standards.md#postgresql-orm-and-dto-conventions), [Session/transaction ownership](conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership), [runtime factory](guides/postgres-connection.md) and [Alembic migration](guides/postgres-alembic.md) |
| R2 local admin CLI, fixture seeding and SQL/log correlation | [README current CLI boundary](../README.md), [PostgreSQL runtime connections](guides/postgres-connection.md), [as-built architecture](architecture.md) and source/tests; admin is PostgreSQL-only, with deprecated `--postgres` no-op |
| Architecture, package ownership or cross-module interfaces | [As-built architecture](architecture.md), [import policy](../architecture/policy.toml) and related tests; the trusted Prepare/Publish worker is under `agent_workflow.workers.trusted_git` |
| R2 design and remaining Clean Architecture migration | [R2 blueprint](architecture/r2-clean-architecture-blueprint.md) (**target; selected Task/Review slices already merged**) and [R2 five-gate contract](architecture/r2-quality-gates-and-tests.md) (**not yet enforced full-tree; R2-QG-01 audit planned**) |
| Worktrees and manual Git commands | [Local worktree guide](guides/local-worktrees.md) |
| Trusted HTTPS provider transport | [Provider HTTPS transport](guides/provider-https-transport.md) |
| Legacy Controller/Docker recovery | [Phase 3.1 runbook](phase-3.1.md) and [dispatch/recovery investigation guide](guides/dispatch-recovery.md) — dated Phase 3.1 observations, verify against current source before running any command |
| OAuth / GitHub integration | [OAuth runbook](auth0-oauth.md), relevant integration source/tests |
| Product orchestration features and gates | [V1 scope contract](v1-scope-and-gates.md), then the task-specific product specs |
| Implementing the product's Code, Test, Publish, QC or Reviewer stages | [V1 scope and gate contract](v1-scope-and-gates.md#3-execution-gates--strictly-one-atomic-task-at-a-time) and relevant source/tests — **only when implementing those features** |
| Task scope and PR handoff | [Task contract](templates/agent-task-contract.md), [one PR handoff template](templates/handoff.md), [TL review checklist](templates/tl-review-checklist.md) |
| Manual Tech Lead onboarding and replacement | [Tech Lead operating procedure](guides/techlead-operating-procedure.md) — authority, task assignment, exact-HEAD review, merge and takeover |
| Historical design, dated operator steps and audit evidence | [CL-12 architecture ADR](adr/CL-12-architecture.md), [historical Phase 2.2 guide](my-wfc.md), [historical handoffs](history/handoff-log.md) — preserve provenance, do not treat old target proposals or SQLite commands as current operations |

**Evidence rules:** The authorized task controls **write paths**, not which safe repo files can be read. The current merged source, task AC, diff and CI run scope are stronger evidence of behavior than stale documents. A linked design is not proof of implementation. If guidance conflicts, report it to the Tech Lead instead of picking the most convenient rule.

**Document maintenance:** Keep this index a router; avoid status snapshots and copying convention text into it. Update links when deleting or moving referenced docs.
