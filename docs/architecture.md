# Agent Workflow architecture — canonical entrypoint

**Verified source baseline:** `main@33cebc9d7b53094477c48e24852cc06a936b9bbe` after [PR #88](https://github.com/builacviet/agent-workflow/pull/88), 2026-11-19. This page distinguishes **merged source**, **deployment/operational evidence**, **legacy code to retire**, and **proposed R2**. Merged source and CI are **not** proof of an activated production system.

**Governance:** [ADR-AW-001](adr/AW-001-clean-architecture-governance.md) ([merged PR #40](https://github.com/builacviet/agent-workflow/pull/40)), [exact architecture import policy](../architecture/policy.toml), [Worker Runtime contract](architecture/worker-runtime-plugins.md) and [task contract](templates/agent-task-contract.md). [R2 Blueprint](architecture/r2-clean-architecture-blueprint.md) and [R2 Quality/Test Contract](architecture/r2-quality-gates-and-tests.md) are **proposed** in Draft PR #90 until Owner merge, not features already implemented.

## CURRENT — as-built source on main

The active Python distribution remains under `src/agent_workflow/`. [PR #88](https://github.com/builacviet/agent-workflow/pull/88) moved trusted Git worker code into the package and removed `src/my_wfc/` and `src/order_service/`. The target feature-sliced `application/*/use_cases` and `interfaces/` layout is not yet implemented.

| Package / process | Merged responsibility and limitation |
| --- | --- |
| `domain/` | Contracts, workflow states/identity and CI evidence; `domain/service.py` still mixes task handoff, review business rules, database transactions and legacy SQLite defaults. It is **not** yet a pure Domain. |
| `storage/` | Legacy SQLite persistence alongside **implemented PostgreSQL** workflow, dispatch, CI and webhook repositories, ORM models and sync `PostgresFactory`. Repository Protocol placement and cross-layer coupling still need refactoring. |
| `infrastructure/` | PostgreSQL leadership/advisory-lock implementation; other adapters remain distributed under legacy packages. |
| `composition/` | Implemented PostgreSQL MCP, Controller, CI-watcher, dispatch and worker wiring. Still not the clean, feature-specific `database.py` / `*_deps.py` structure proposed for R2. |
| `bootstrap/` | PostgreSQL MCP provider/config activation seam; overlaps conceptually with `composition/`. |
| `integrations/` | GitHub authentication/CI checks and webhook transport/coordination, with some legacy Storage coupling. |
| `mcp/` | MCP tools, Auth0 OAuth / bearer transport and Tech Lead GitHub/Notion integration endpoints; its server wires a **PostgreSQL** workflow provider. |
| `orchestration/` | Trusted Controller, CI-watcher coordination, role profiles and existing CLI paths. |
| `runners/` | Docker phase coordination, sandbox/watchdog and runtime plugin selection for approved Dev/Code. A runner import cycle remains in the exact architecture debt inventory. |
| `workers/trusted_git/` | Trusted Prepare/Publish worker with GitHub App credentials, Git verification, snapshots and path safety. It is **not** a general-purpose untrusted Coder agent or database client. |
| Root `agent_workflow/*.py` wrappers | Older compatibility import/CLI entrypoints, not a dedicated architecture layer. |

**Architecture debt remains:** `domain/service.py` still imports Storage/GitHub/paths through enumerated legacy exceptions; `architecture/policy.toml` also tracks other legacy imports and one runner cycle. `tests/test_architecture_policy.py` rejects **new** forbidden edges and stale exceptions; a passing gate is not a claim that all existing dependencies are clean.

## CURRENT — PostgreSQL, legacy SQLite, deployment and tests

- **PostgreSQL source exists:** Alembic migrations, PostgreSQL workflow/CI/dispatch/webhook adapters, sync `PostgresFactory` with schema-revision validation, transaction/session ownership, leadership and concrete Controller/Worker composition. `composition/postgres_dispatch_entrypoint.py` supplies an explicit one-shot PostgreSQL execution path. These are merged **implementation**, not proof that the Owner has activated it live.
- **Tracked Compose files differ:** `compose.postgres.dev.yaml` supplies an isolated development PostgreSQL service. Root `compose.yaml` still declares MCP, webhook, router and optional ngrok with **legacy SQLite state mounts**, and no PostgreSQL or Controller service. Meanwhile `mcp/server.py` now builds a PostgreSQL provider; **do not claim the unmodified root Compose stack is a verified end-to-end PostgreSQL deployment**. Actual live configuration, credentials, startup and DB cutover need independent evidence.
- **SQLite is retirement debt, not an R2 target:** legacy SQLite files remain today, but the Owner's R2 decision is **PostgreSQL-only forward development**. Do not add new SQLite tests/coverage, adapters, data models, facades, migration compatibility layers or feature parity. Inventory the remaining consumers to remove them safely in a separately approved cleanup/cutover. Keep only the minimum existing safety checks needed to avoid breaking an active path **until it is explicitly retired**; no broad SQLite refactor or remediation work.
- **Runtime:** trusted host Controller/CI watcher CLI coordination, and Docker Prepare → Code → Test → Publish phases. Code/Test is credential-free; trusted Prepare/Publish alone owns GitHub App credentials. Runtime adapter selection currently covers approved Dev/Code; general role/phase plugins and a pluggable Docker/Podman backend are future work. Deployed systemd units or full running product E2E are **not verified**.
- **CI currently enforced:** `.github/workflows/v1-ci.yml` runs push/PR Python tests, exact architecture policy tests, **changed-file-only Ruff**, ephemeral PostgreSQL migration/adapter/leadership tests and development-Compose smoke. The merged [main CI run](https://github.com/builacviet/agent-workflow/actions/runs/44432307839) succeeded. R2's **five new quality gates are not fully enabled yet**: whole-source Ruff+C901, Import Linter, Mypy Strict, separate ≥90% core line/branch coverage and Alembic model drift `check`.
- **Verification limit:** Existing isolated tests and historical OAuth/webhook smoke evidence do not prove the full real Worker → GitHub publish → trusted CI → Controller/review process or production activation. Do not infer external side-effect atomicity from a successful database test.

## CURRENT — correctness constraints

- **CI repair fixed versus old documentation:** The previous statement that `READY_IN_REVIEW` always rejected a replacement SHA after failed CI is obsolete. `WorkflowService.register_task_handoff` implements a bounded `CI_REPAIR_HANDOFF` path with a **new SHA**, existing handoff provenance and trusted CI repair tracking; see `tests/test_r2_00_characterization.py`. Exact-HEAD / sticky failure policy remains, and this fix does not certify all recovery scenarios.
- **Review:** claim/submit validate current HEAD, verified CI, lease, findings/prior resolutions, effort (when required) and two-round limit. `AppConfig.from_env()` validates PostgreSQL DSN and CI gate. A local-test bypass is not authorization to publish unverified work.
- **PostgreSQL/external operations:** preserve atomic DB transitions, idempotency, row locking, timeouts, leadership fencing and explicit reconciliation of uncertain GitHub/Docker effects. Advisory locks alone cannot fence external effects.
- **Security:** preserve Auth0/bearer authorization rules, distinct controller/reviewer/worker identities, webhook signature handling, credential-free Code/Test and independent trusted Worker secrets. Import linting alone cannot prove runtime isolation.

## TARGET — R2 Clean Architecture, not yet implemented

**Pinned reference:** [Owner's `clean_arch_demo` @ `b274fe4`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145). Borrow its readable source structure, Use Cases/`Protocol`, dependency composition, PostgreSQL SessionFactory, AppConfig and CI quality discipline; **do not copy unrelated RabbitMQ/Celery or trade away workflow security**.

- **One namespace:** refactor in the existing `src/agent_workflow/` tree. No `agent_workflow_v2`; removing only the outer `src/` is an optional, separate later packaging change.
- **`domain/`:** pure entities, values, policies and transitions grouped by task, review, dispatch, CI and runtime features; no SQLAlchemy/Pydantic/network/IO.
- **`application/`:** feature-sliced named Use Cases, typed DTOs, `Protocol` repositories/runtime ports and explicit UnitOfWork/transaction intent, not concrete infrastructure.
- **`infrastructure/`:** **PostgreSQL** SQLAlchemy repositories/models, Sync + optional Async SessionFactory, UoW, validated `AppConfig` and secret-safe GitHub/Docker/observability adapters. No new SQLite implementation.
- **`interfaces/` (not `presentation/`):** thin inbound MCP, webhook and CLI handlers.
- **`composition/`:** easy-to-read `database.py`, `task_deps.py`, `review_deps.py`, `dispatch_deps.py`, `runtime_deps.py` and similar pure dependency wiring, modeled on the demo's `dependencies/database.py` and `*_deps.py`. Do not create DB sessions at import time or let handlers construct repositories.
- **`workers/trusted_git/`:** focused Prepare/Publish, Git/GitHub, auth and snapshot modules without a second nested Clean Architecture stack; retain the independent GitHub App authority and **no workflow DB**.
- **Observability:** safe JSON structured logs, validated request/correlation IDs across process boundaries, per-service liveness/readiness, failures/secret-redaction tests.
- **Five quality gates:** full Ruff+C901, Import Linter+AST/negative fixtures, Mypy Strict, meaningful unit tests for every new Use Case plus ≥90% **Domain/Application** line and branch coverage, and Alembic PostgreSQL schema drift detection. **Legacy SQLite code is explicitly excluded from new R2 coverage investment**; retire it, do not improve it.
- **Incremental source PRs:** PostgreSQL/domain/application first, then bounded integration, interfaces and enforcement. Remove unused legacy SQLite when eligible; do not preserve it forever for backwards compatibility.

Details: [full R2 folder/use-case skeleton](architecture/r2-clean-architecture-blueprint.md) and [quality/architecture unit-test matrix](architecture/r2-quality-gates-and-tests.md). These documents alone authorize **no** source, CI, schema, policy, privilege or deployment edit.

## Documentation precedence

1. **Current truth:** merged source at a verified HEAD, tests/CI with known scope, and independently observed running configuration. Historical README/runbooks and synthetic tests are not deployment attestations.
2. **Task authority:** [V1 scope and gates](v1-scope-and-gates.md), approved Notion task/scope and [manual development task contract](templates/agent-task-contract.md). Protected changes need focused TL + applicable Owner approval; **Owner alone merges**.
3. **R2 proposed:** [architecture blueprint](architecture/r2-clean-architecture-blueprint.md) and [quality contract](architecture/r2-quality-gates-and-tests.md) from Draft PR #90. Treat implementation as future work.
4. **History only:** CL-12 SQLite/Compose/Podman concepts ([architecture](adr/CL-12-architecture.md), [deployment](adr/CL-12-deployment.md), [persistence](adr/CL-12-persistence.md)) and [old my-wfc guide](my-wfc.md)/[migration notes](migration-my-wfc.md) preserve provenance, **not** current implementation guidance.
