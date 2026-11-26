# Agent Workflow architecture — canonical entrypoint

**Source baseline:** merged [main@fb552670bf2b](https://github.com/builacviet/agent-workflow/tree/fb552670bf2b9985e52c862482f3af613d3263a6) after #150. This page is as-built source plus remaining targets, not proof of production activation. [Governance ADR](adr/AW-001-clean-architecture-governance.md), [import policy](../architecture/policy.toml), [R2 target](architecture/r2-clean-architecture-blueprint.md), [quality target](architecture/r2-quality-gates-and-tests.md).

## CURRENT — as-built source on main

One distribution, `src/agent_workflow/`. SQLite runtime persistence and WorkflowService retired (#113–119); PostgreSQL aliases retired (#120); old Pydantic core contracts deleted (#121). Task Handoff uses canonical UoW (#122–123). Review claim/submit and transactional queue recovery migrated (#125–126); legacy workflow repository and `storage/` package removed (#127–128). Typed dispatch claims and architecture sync (#129–130), CI business writes/ACK (#131–133), read scope/query normalization (#134–136), and persistence ownership/legacy API retirement/settings and lock constants (#137–143) are merged. MCP authentication, outbound Tech Lead adapters, inbound tools, architecture governance, entrypoint and DI migration and legacy namespace retirement (#144–150) are merged.

| Package | Responsibility / remaining debt |
| --- | --- |
| Domain | Pure business values, policies, states/identity and errors. Canonical WorkflowStatus, DispatchStatus and CiVerdict. |
| Application | Typed feature Use Cases and ports: task authorization/handoff/assignment, review context/result/queue/claim/submit, dispatch, CI, webhook, admin. Review claim/submit, queue recovery, task/admin/handoff and CI track/record coordinate business transaction intent through the canonical UoW. |
| Infrastructure/database | Canonical SQLAlchemy models, PostgresFactory, SqlAlchemyUnitOfWork, repositories and readers. Task authorization/handoff/admin and review writes/queue recovery use canonical UoW. Independent review context/result/assignment use read-only snapshots. CI readers and independent ACK/webhook writes own fixed short scopes; dispatch ledger owns atomic writes using the concrete UoW and an independent pre-launch read. Shared settings live in `transaction_options.py`, stable transaction lock keys in `lock_keys.py`. |
| Composition | Construct/inject dependencies and manage resource scopes only. MCP providers in `composition/mcp_deps.py`; existing Controller/Webhook launcher-style Composition modules are transitional debt. |
| Interfaces (Presentation) | Own inbound entrypoints, transport startup/shutdown, requests/responses and invocation of injected Use Cases. MCP is canonical in `interfaces/mcp/`, with explicit server build, Auth0/bearer and TL scope unchanged. Controller/CI and Admin/Webhook entrypoint cleanup remains. |
| Legacy Bootstrap (transitional) | Remaining Controller startup wiring only as migration debt, not a target layer; MCP Bootstrap and legacy MCP package/root alias retired. |
| integrations / orchestration / runners | Existing provider clients, role profiles and Controller/Worker runtime; remaining import exceptions/cycle tracked by exact architecture policy. |
| workers/trusted_git | Trusted Prepare/Publish, GitHub App credentials, Git/snapshot verification. No workflow DB client in the untrusted Code/Test execution environment. |
| observability | Local admin JSON logs and validated correlation ID/SQL comment; broader tracing/health remains target. |

## CURRENT — PostgreSQL and verification

[Canonical DB access policy](conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership): Reader-owned read-only scope, Repository-owned independent atomic write, Application-owned business transaction intent through IUnitOfWork. One table alone does not decide. UoW physical commit occurs on successful context exit; #123 centralizes deadline checks. Session leadership remains separate. Preserve exact SHA, repair evidence and two-repair cap, lease/round/effort/CI rules, atomic rollback, fencing and explicit reconciliation. Queue listing recovers expired leases and selects eligible items within one UoW; commit intent follows successful selection only when recovery mutated state (#126). Independent reads remain read-only. CI track/record coordinate business writes through the fixed CI UoW profile; ACK and webhook writes retain independent atomic scopes. Dispatch uses immutable typed claims and adapter-owned atomic operations with preserved lock/deadline guards. Legacy auto-commit Session wrappers are retired; factory lifecycle, UoW coordination, transaction settings and lock constants have separate owners. See the runtime guide for concrete scope and timeout boundaries.

PostgresFactory requires Alembic head `pg02d_webhook_digest`; local admin uses validated loopback PG_RUNTIME_DSN and explicit provision/seed/reset commands (deprecated --postgres no-op; exit 2 validation/conflict, 75 temporary failure). No implicit schema migration. [Runtime guide](guides/postgres-connection.md).

**Deployment not verified:** `compose.postgres.dev.yaml` is isolated development DB; root `compose.yaml` still includes obsolete WFC_DB_PATH/SQLite bind mount for MCP, not a verified PostgreSQL deployment. Do not activate services/tunnel or modify ignored env/secrets without separate Owner authorization. Trusted Prepare → Code → Test → Publish; Code/Test credential-free; real external E2E and production activation not established.

**Current CI:** changed-file Ruff, exact AST/import ratchet, full pytest, PG16 migration/adapter/leadership/integration, Compose smoke. #150 [run 45789437620](https://github.com/builacviet/agent-workflow/actions/runs/45789437620) and [run 45788947368](https://github.com/builacviet/agent-workflow/actions/runs/45788947368) both report success for all three jobs and associated PR HEAD `4be2c588d5ab553a5b7ae1efe66afa3a651a2c0f`; actual synthetic checkout SHA and individual test counts were not independently verified here.

**Historical CI evidence:** #143 final [CI run](https://github.com/builacviet/agent-workflow/actions/runs/45733532539) passed all three jobs on PR merge ref `d2ed91b78e0a61536f0aecc8b3fc608d0922299b` (HEAD `123abf67e1c2ae750fc94edf9d6b6bdfb56ca206`): Ruff four files, architecture 51 passed, pytest 1206 passed/151 skipped/39 subtests/1 warning, PG job 194 passed/1 skipped, Compose 32 passed. These groups overlap; skipped checks are not verified. Exact exceptions and one runner cycle remain; green CI does not prove completed architecture or deployed isolation.

## TARGET — remaining R2

- Remaining sequence: CLEAN-05E Controller/CI wiring and Bootstrap retirement, CLEAN-05F Admin/Webhook entrypoints, CLEAN-06 external adapters, CLEAN-07B/C runtime/Worker/Dispatch execution wiring. Continue remaining config/entrypoint cleanup in bounded tasks; no new parallel namespace or unneeded abstraction. [Blueprint](architecture/r2-clean-architecture-blueprint.md) uses the pinned Owner demo as reference, not a drop-in implementation.
- R2-TEST-01: audit test collection, redundancy and obsolete coverage after CLEAN-09, retaining meaningful regression/security/concurrency evidence; no arbitrary test-count target.
- R2-QG-01: full-tree Ruff+C901, Import Linter+existing AST, Mypy Strict, meaningful use-case unit tests and separate ≥90% Domain/Application line and branch coverage, Alembic drift check. Mandatory full-tree gates not yet enforced. No blanket ignores.
- Broader health/tracing, optional async only with actual consumer, and external E2E/deployment require separate approval and evidence.

## Documentation precedence

Approved task + [AGENTS.md](../AGENTS.md) control authorization; merged code and observed tests/config establish current behavior; [engineering standards](conventions/engineering-standards.md) define conventions. [R2 blueprint](architecture/r2-clean-architecture-blueprint.md) and [quality contract](architecture/r2-quality-gates-and-tests.md) are targets. [V1 product gates](v1-scope-and-gates.md) are not manual development steps. CL-12 ADRs, old SQLite runbooks, task records and handoff logs preserve history only. Use [index](INDEX.md) on demand. Docs do not authorize source, CI, deployment or privilege changes.
