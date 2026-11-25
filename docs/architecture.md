# Agent Workflow architecture — canonical entrypoint

**Source baseline:** merged [main@14fe974e67f5](https://github.com/builacviet/agent-workflow/tree/14fe974e67f56175bed3710b20bc36fb69b79cd0) after #128. This page is as-built source plus remaining targets, not proof of production activation. [Governance ADR](adr/AW-001-clean-architecture-governance.md), [import policy](../architecture/policy.toml), [R2 target](architecture/r2-clean-architecture-blueprint.md), [quality target](architecture/r2-quality-gates-and-tests.md).

## CURRENT — as-built source on main

One distribution, `src/agent_workflow/`. SQLite runtime persistence and WorkflowService retired (#113–119); PostgreSQL aliases retired (#120); old Pydantic core contracts deleted (#121). Task Handoff uses canonical UoW (#122–123). Review claim/submit and transactional queue recovery migrated (#125–126); legacy workflow repository and `storage/` package removed (#127–128).

| Package | Responsibility / remaining debt |
| --- | --- |
| Domain | Pure business values, policies, states/identity and errors. Canonical WorkflowStatus, DispatchStatus and CiVerdict. |
| Application | Typed feature Use Cases and ports: task authorization/handoff/assignment, review context/result/queue/claim/submit, dispatch, CI, webhook, admin. Review claim/submit and queue recovery use canonical UoW. CI business write coordination remains to migrate. |
| Infrastructure/database | Canonical SQLAlchemy models, PostgresFactory, SqlAlchemyUnitOfWork, repositories and readers. Task authorization/handoff/admin and review writes/queue recovery use canonical UoW. Independent review context/result/assignment use read-only snapshots. CI/webhook own current short scopes; dispatch ledger resides in `infrastructure/database/repositories/dispatch_ledger.py`. |
| Composition / Bootstrap | Scoped dependency construction and lifecycle. Interfaces call Use Cases; some legacy launchers remain. |
| Interfaces / MCP | CLI request/response validation, MCP review schemas, webhook HTTP. Old MCP/root entrypoints remain to migrate; preserve Auth0/bearer and TL scope. |
| integrations / orchestration / runners | Existing provider clients, role profiles and Controller/Worker runtime; remaining import exceptions/cycle tracked by exact architecture policy. |
| workers/trusted_git | Trusted Prepare/Publish, GitHub App credentials, Git/snapshot verification. No workflow DB client in the untrusted Code/Test execution environment. |
| observability | Local admin JSON logs and validated correlation ID/SQL comment; broader tracing/health remains target. |

## CURRENT — PostgreSQL and verification

[Canonical DB access policy](conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership): Reader-owned read-only scope, Repository-owned independent atomic write, Application-owned business transaction intent through IUnitOfWork. One table alone does not decide. UoW physical commit occurs on successful context exit; #123 centralizes deadline checks. Session leadership remains separate. Preserve exact SHA, repair evidence and two-repair cap, lease/round/effort/CI rules, atomic rollback, fencing and explicit reconciliation. Queue listing recovers expired leases and selects eligible items within one UoW; commit intent follows successful selection only when recovery mutated state (#126). Independent reads remain read-only. CI record evidence combines observation/verdict writes; its business transaction migration remains pending. Dispatch relocation (#128) preserves existing adapter-owned atomic operations, locks and deadline guards; the typed immutable claim boundary is not yet merged.

PostgresFactory requires Alembic head `pg02d_webhook_digest`; local admin uses validated loopback PG_RUNTIME_DSN and explicit provision/seed/reset commands (deprecated --postgres no-op; exit 2 validation/conflict, 75 temporary failure). No implicit schema migration. [Runtime guide](guides/postgres-connection.md).

**Deployment not verified:** `compose.postgres.dev.yaml` is isolated development DB; root `compose.yaml` still includes obsolete WFC_DB_PATH/SQLite bind mount for MCP, not a verified PostgreSQL deployment. Do not activate services/tunnel or modify ignored env/secrets without separate Owner authorization. Trusted Prepare → Code → Test → Publish; Code/Test credential-free; real external E2E and production activation not established.

**Current CI:** changed-file Ruff, exact AST/import ratchet, full pytest, PG16 migration/adapter/leadership/integration, Compose smoke. #128 push and merge-ref runs passed; see [push](https://github.com/builacviet/agent-workflow/actions/runs/45560274137) and [PR](https://github.com/builacviet/agent-workflow/actions/runs/45560283639). Exact exceptions and one runner cycle remain; green CI does not prove completed architecture or deployed isolation.

## TARGET — remaining R2

- Finish DB migration: typed dispatch (CLEAN-04B), CI business writes (DB-05), read scope/timeout normalization (DB-04), independent writes and final persistence scope retirement (DB-06). Retain locks/timeouts/durability and external-effect ordering.
- Migrate remaining interfaces, provider/runtime adapters and config/entrypoints in bounded tasks; no new parallel namespace or unneeded abstraction. [Blueprint](architecture/r2-clean-architecture-blueprint.md) uses the pinned Owner demo as reference, not a drop-in implementation.
- R2-TEST-01: audit test collection, redundancy and obsolete coverage after CLEAN-09, retaining meaningful regression/security/concurrency evidence; no arbitrary test-count target.
- R2-QG-01: full-tree Ruff+C901, Import Linter+existing AST, Mypy Strict, meaningful use-case unit tests and separate ≥90% Domain/Application line and branch coverage, Alembic drift check. Mandatory full-tree gates not yet enforced. No blanket ignores.
- Broader health/tracing, optional async only with actual consumer, and external E2E/deployment require separate approval and evidence.

## Documentation precedence

Approved task + [AGENTS.md](../AGENTS.md) control authorization; merged code and observed tests/config establish current behavior; [engineering standards](conventions/engineering-standards.md) define conventions. [R2 blueprint](architecture/r2-clean-architecture-blueprint.md) and [quality contract](architecture/r2-quality-gates-and-tests.md) are targets. [V1 product gates](v1-scope-and-gates.md) are not manual development steps. CL-12 ADRs, old SQLite runbooks, task records and handoff logs preserve history only. Use [index](INDEX.md) on demand. Docs do not authorize source, CI, deployment or privilege changes.
