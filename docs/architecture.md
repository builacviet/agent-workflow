# Agent Workflow architecture — canonical entrypoint

**Verified source baseline:** `main@70b333be08d87a90190cdb06cdde0a9b1cb635f3` after Owner-merged [PR #98](https://github.com/builacviet/agent-workflow/pull/98), 2026-11-20. This page distinguishes **merged PostgreSQL-first R2 slices**, **still-active legacy/retirement debt**, **target design**, and **separately verified deployment evidence**. A merged feature or passing CI is **not** proof that the complete workflow is activated in production.

**Governance:** [ADR-AW-001](adr/AW-001-clean-architecture-governance.md) ([merged PR #40](https://github.com/builacviet/agent-workflow/pull/40)), [exact architecture import policy](../architecture/policy.toml), [Worker Runtime contract](architecture/worker-runtime-plugins.md) and [task contract](templates/agent-task-contract.md). [R2 Blueprint](architecture/r2-clean-architecture-blueprint.md) and [R2 Quality/Test Contract](architecture/r2-quality-gates-and-tests.md) are **merged design/target contracts**, not proof all components or the five mandatory quality gates exist. R2-QG-01 enforcement is still pending audit and explicit protected-scope approval.

## CURRENT — as-built source on main

The active Python distribution remains under `src/agent_workflow/`. [PR #88](https://github.com/builacviet/agent-workflow/pull/88) moved the trusted Git worker and retired the separate `src/my_wfc/` and `src/order_service/` package trees. Since then, [PR #96](https://github.com/builacviet/agent-workflow/pull/96), [PR #97](https://github.com/builacviet/agent-workflow/pull/97), and [PR #98](https://github.com/builacviet/agent-workflow/pull/98) have implemented **bounded, feature-local** Task/Review, admin CLI and observability slices. This is **not** a completed repository-wide Clean Architecture conversion.

| Package / process | Merged responsibility and limitation |
| --- | --- |
| `domain/` | Contracts, workflow states/identity and CI evidence; `domain/states.py` already defines canonical `WorkflowStatus`, `DispatchStatus` and `CiVerdict` as `StrEnum` (PR #43). `domain/service.py` still mixes task handoff, review business rules, database transactions and legacy SQLite defaults; it is **not** yet a pure Domain. |
| `application/` | Implemented typed Task authorization/admin fixture Use Cases, Review-context query Use Case/immutable DTOs, backend-independent persistence errors and repository/reader/UoW ports. Only these selected slices are cut over. |
| `storage/` | Legacy SQLite files and earlier PostgreSQL adapters still coexist; not all callers have migrated to the newer Infrastructure Repository/Reader/UoW boundaries. |
| `infrastructure/` | Canonical SQLAlchemy PostgreSQL models, `PostgresFactory`, scoped `SqlAlchemyUnitOfWork`, Task/Review/fixture repositories, feature-specific review reader, leadership, and safe JSON logging. Engine correlation comments use a validated ContextVar ID. |
| `composition/` | PostgreSQL MCP/Controller/CI-watcher/worker wiring plus implemented `task_deps.py` and `review_deps.py` feature-level dependency assembly. Other planned feature composition remains future work. |
| `interfaces/` | TaskSpec Pydantic CLI transport validation is implemented under `interfaces/cli/task_spec.py`; older MCP/webhook/controller entrypoints have not all been moved into this layer. |
| `observability/` | `correlation.py` provides safe ContextVar IDs for the PostgreSQL-only local admin CLI; broad multi-service tracing/health is not yet implemented. |
| `bootstrap/` | PostgreSQL MCP provider/config activation seam; retains existing composition responsibilities. |
| `integrations/` | GitHub authentication/CI checks and webhook transport/coordination, with some legacy Storage coupling. |
| `mcp/` | MCP tools, Auth0 OAuth / bearer transport and Tech Lead GitHub/Notion integration endpoints; its server wires a **PostgreSQL** workflow provider. |
| `orchestration/` | Trusted Controller, CI-watcher coordination, role profiles and existing CLI paths. |
| `runners/` | Docker phase coordination, sandbox/watchdog and runtime plugin selection for approved Dev/Code. A runner import cycle remains in the exact architecture debt inventory. |
| `workers/trusted_git/` | Trusted Prepare/Publish worker with GitHub App credentials, Git verification, snapshots and path safety. It is **not** a general-purpose untrusted Coder agent or database client. |
| Root `agent_workflow/*.py` wrappers | Compatibility imports and CLI entrypoints; `admin.py` is PostgreSQL-only (provision, history/synthetic seed/reset), accepts deprecated `--postgres` as a no-op, maps operator errors to exit `2` and temporary failures to exit `75`. |

**Architecture debt remains:** `domain/service.py` still imports Storage/GitHub/paths through enumerated legacy exceptions; `architecture/policy.toml` also tracks other legacy imports and one runner cycle. `tests/test_architecture_policy.py` rejects **new** forbidden edges and stale exceptions; a passing gate is not a claim that all existing dependencies are clean.

## CURRENT — PostgreSQL, legacy SQLite, deployment and tests

- **PostgreSQL source exists:** Alembic migrations, older PostgreSQL workflow/CI/dispatch/webhook adapters, new Task/Review SQLAlchemy repositories/reader/UoW, and a schema-checking `PostgresFactory` expecting `pg02d_webhook_digest` (as of this baseline). Local admin uses PostgreSQL only. `composition/postgres_dispatch_entrypoint.py` offers a one-shot Controller path. These are merged implementations, **not** proof of live Owner activation.
- **Tracked Compose files differ:** `compose.postgres.dev.yaml` supplies an isolated development PostgreSQL service. Root `compose.yaml` still declares MCP, webhook, router and optional ngrok with **legacy SQLite state mounts**, and no PostgreSQL or Controller service. Meanwhile `mcp/server.py` now builds a PostgreSQL provider; **do not claim the unmodified root Compose stack is a verified end-to-end PostgreSQL deployment**. Actual live configuration, credentials, startup and DB cutover need independent evidence.
- **SQLite is retirement debt, not an R2 target:** legacy SQLite files remain today, but the Owner's R2 decision is **PostgreSQL-only forward development**. Do not add new SQLite tests/coverage, adapters, data models, facades, migration compatibility layers or feature parity. Inventory the remaining consumers to remove them safely in a separately approved cleanup/cutover. Keep only the minimum existing safety checks needed to avoid breaking an active path **until it is explicitly retired**; no broad SQLite refactor or remediation work.
- **Runtime:** trusted host Controller/CI watcher CLI coordination, and Docker Prepare → Code → Test → Publish phases. Code/Test is credential-free; trusted Prepare/Publish alone owns GitHub App credentials. Runtime adapter selection currently covers approved Dev/Code; general role/phase plugins and a pluggable Docker/Podman backend are future work. Deployed systemd units or full running product E2E are **not verified**.
- **CI currently enforced:** `.github/workflows/v1-ci.yml` runs push/PR Python tests, exact AST architecture ratchet, **changed-file-only Ruff**, PostgreSQL migration/adapter/leadership tests and development-Compose smoke. [PR #98 push CI](https://github.com/builacviet/agent-workflow/actions/runs/44748283033) and [merge-ref CI](https://github.com/builacviet/agent-workflow/actions/runs/44748293854) both passed on the reviewed SHA. R2-QG-01's **five full-tree gates remain unimplemented as mandatory gates**: Ruff+C901, Import Linter+AST, Mypy Strict, separate ≥90% Domain/Application line-and-branch coverage, and meaningful Alembic ORM drift `check`.
- **Verification limit:** Existing isolated tests and historical OAuth/webhook smoke evidence do not prove the full real Worker → GitHub publish → trusted CI → Controller/review process or production activation. Do not infer external side-effect atomicity from a successful database test.

## CURRENT — correctness constraints

- **CI repair fixed versus old documentation:** The previous statement that `READY_IN_REVIEW` always rejected a replacement SHA after failed CI is obsolete. `WorkflowService.register_task_handoff` implements a bounded `CI_REPAIR_HANDOFF` path with a **new SHA**, existing handoff provenance and trusted CI repair tracking; see `tests/test_r2_00_characterization.py`. Exact-HEAD / sticky failure policy remains, and this fix does not certify all recovery scenarios.
- **Review:** claim/submit validate current HEAD, verified CI, lease, findings/prior resolutions, effort (when required) and two-round limit. `AppConfig.from_env()` validates PostgreSQL DSN and CI gate. A local-test bypass is not authorization to publish unverified work.
- **PostgreSQL/external operations:** preserve atomic DB transitions, idempotency, row locking, timeouts, leadership fencing and explicit reconciliation of uncertain GitHub/Docker effects. Advisory locks alone cannot fence external effects.
- **Security:** preserve Auth0/bearer authorization rules, distinct controller/reviewer/worker identities, webhook signature handling, credential-free Code/Test and independent trusted Worker secrets. Import linting alone cannot prove runtime isolation.

## TARGET — remaining R2 Clean Architecture (partially implemented)

**Pinned reference:** [Owner's `clean_arch_demo` @ `b274fe4`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145). Borrow its readable source structure, Use Cases/`Protocol`, dependency composition, PostgreSQL SessionFactory, AppConfig and CI quality discipline; **do not copy unrelated RabbitMQ/Celery or trade away workflow security**.

- **One namespace:** refactor in the existing `src/agent_workflow/` tree. No `agent_workflow_v2`; removing only the outer `src/` is an optional, separate later packaging change.
- **`domain/`:** pure entities, values, policies and transitions grouped by task, review, dispatch, CI and runtime features; no SQLAlchemy/Pydantic/network/IO.
- **`application/`:** feature-sliced named Use Cases, typed DTOs, `Protocol` repositories/runtime ports and explicit UnitOfWork/transaction intent, not concrete infrastructure.
- **`infrastructure/`:** **PostgreSQL** SQLAlchemy repositories/models, Sync + optional Async SessionFactory, UoW, validated `AppConfig` and secret-safe GitHub/Docker/observability adapters. No new SQLite implementation.
- **`interfaces/` (not `presentation/`):** thin inbound MCP, webhook and CLI handlers.
- **`composition/`:** easy-to-read `database.py`, `task_deps.py`, `review_deps.py`, `dispatch_deps.py`, `runtime_deps.py` and similar pure dependency wiring, modeled on the demo's `dependencies/database.py` and `*_deps.py`. Do not create DB sessions at import time or let handlers construct repositories.
- **`workers/trusted_git/`:** focused Prepare/Publish, Git/GitHub, auth and snapshot modules without a second nested Clean Architecture stack; retain the independent GitHub App authority and **no workflow DB**.
- **Observability:** local admin JSON structured logs, safe ContextVar ID and PostgreSQL statement comments are **implemented in PR #98**; cross-process propagation and service-wide liveness/readiness/health remain targets requiring their own approved tasks and verification.
- **Five quality gates (R2-QG-01 target, not yet green full-tree):** full Ruff+C901, Import Linter+existing AST/negative fixtures, Mypy Strict, meaningful Use Case unit tests plus separate ≥90% **Domain/Application** line and branch coverage, and Alembic PostgreSQL ORM/schema drift detection. Retire legacy SQLite only after callers and Owner-approved cutover are verified; do not blanket-ignore active code to make the gates green.
- **Incremental source PRs:** PostgreSQL/domain/application first, then bounded integration, interfaces and enforcement. Remove unused legacy SQLite when eligible; do not preserve it forever for backwards compatibility.

Details: [full R2 folder/use-case skeleton](architecture/r2-clean-architecture-blueprint.md) and [quality/architecture unit-test matrix](architecture/r2-quality-gates-and-tests.md). These documents alone authorize **no** source, CI, schema, policy, privilege or deployment edit.

## Documentation precedence

1. **Current truth:** merged source at a verified HEAD, tests/CI with known scope, and independently observed running configuration. Historical README/runbooks and synthetic tests are not deployment attestations.
2. **Task authority:** [V1 scope and gates](v1-scope-and-gates.md), approved Notion task/scope and [manual development task contract](templates/agent-task-contract.md). Protected changes need focused TL + applicable Owner approval; **Owner alone merges**.
3. **R2 design and remaining work:** [architecture blueprint](architecture/r2-clean-architecture-blueprint.md) and [quality contract](architecture/r2-quality-gates-and-tests.md) were merged as design references. Check the feature slices actually merged in PR #96–98 and the pending R2-QG-01 audit before assuming a target is implemented or enforced.
4. **History only:** CL-12 SQLite/Compose/Podman concepts ([architecture](adr/CL-12-architecture.md), [deployment](adr/CL-12-deployment.md), [persistence](adr/CL-12-persistence.md)) and [old my-wfc guide](my-wfc.md)/[migration notes](migration-my-wfc.md) preserve provenance, **not** current implementation guidance.
