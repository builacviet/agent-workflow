# R2 — Clean Architecture blueprint (target; partially implemented)

**Status:** Merged design reference; selected R2 slices implemented. See [current source status](../architecture.md). This document authorizes **no** code, policy, CI, DB, credential, deployment or runtime edits by itself.
**Source baseline:** `main@33cebc9d7b53094477c48e24852cc06a936b9bbe`, after PR #88 (original design baseline). Target ownership updated by [#148](https://github.com/builacviet/agent-workflow/pull/148); MCP source migration completed through [#150](https://github.com/builacviet/agent-workflow/pull/150). **Reference:** [Owner's clean_arch_demo](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145).
**Related:** [ADR-AW-001](../adr/AW-001-clean-architecture-governance.md), [current architecture](../architecture.md), [R2 quality and test contract](r2-quality-gates-and-tests.md), [worker runtime contract](worker-runtime-plugins.md), [development task contract](../templates/agent-task-contract.md).

## 1. Outcome and rules of engagement

Make workflow business behavior discoverable by **feature and use case**, as in the demo; do not merely shuffle modules or rename interfaces. Keep **one Python distribution and one import namespace, `agent_workflow`**; do **not** create `agent_workflow_v2`, a second application, a second database truth, or a parallel deployment. Use **`interfaces`** (not `presentation`) and a single **`composition`** directory with understandable `database.py` and `<feature>_deps.py` factories.

- `domain` = entities, value objects, transitions, policies and domain errors; plain Python, no Pydantic/SQLAlchemy/GitHub/MCP/Docker/IO dependencies.
- `application` = named use cases, typed input/output DTOs and task-oriented ports (`typing.Protocol`). It owns transaction **intent** and authorization decisions, not concrete DB sessions, HTTP, Git, Docker or framework exceptions. Model validation can happen in interface DTOs; domain invariant checks remain pure.
- `infrastructure` = PostgreSQL repositories, ORM models, sync/async SessionFactory, UoW implementation, GitHub/network/engine adapters, configuration and observability. It implements application ports.
- `interfaces` = Presentation: own executable entrypoints, transport startup/registration/shutdown, authenticate, validate transport payload, obtain injected use cases from explicit Composition factories, invoke them directly and map errors/responses. No concrete Infrastructure imports, SQL or business decisions in handlers.
- `composition` = **dependency assembly only**, named `database.py`/`task_deps.py`/`review_deps.py`/`dispatch_deps.py`/`runtime_deps.py`/`health_deps.py`; builds factory/UoW/repository/use-case graph with a controlled lifetime. No feature behavior, policy duplication, transaction commits or invocation of inbound CLI handlers/use-case `execute()` here.

**Five-part target:** Domain, Application, Infrastructure, Composition and Interfaces (Presentation). Presentation may import explicit Composition factories (for example, FastAPI `Depends`); Composition constructs/injects and manages dependency scopes, not Presentation handlers or Use Case execution. The Controller executable lives in `interfaces/cli/controller.py`. Its CI and Dispatch dependency scopes live in `composition/ci_deps.py` and `composition/dispatch_deps.py`. The Webhook executable lives in `interfaces/webhook/server.py` and obtains its scoped use case from `composition/webhook_deps.py`. The import policy forbids Composition from importing Interfaces.
- `workers/trusted_git` = separate trusted Prepare/Publish executable and GitHub App authority. Split by responsibility, **not** a second nested Domain/Application stack. Its sole credential boundary remains independent of credential-free Code/Test containers and workflow DB.

**PostgreSQL-only R2:** SQLite runtime persistence retired in #113–119. Do not resurrect it or add coverage/compatibility shims. Retain historical evidence; actual deployment/cutover requires independent evidence. Use the [canonical DB access policy](../conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership) for all remaining DB migrations.

**Source layout choice:** For the first R2 refactor PRs, retain the current `src/agent_workflow/` physical layout and `agent_workflow.*` import names. The final optional shorter tree `agent_workflow/` at repository root (removing **only `src/`**) is a distinct, late packaging change requiring isolated approval, CI, wheel/Docker checks and migration verification. Do not combine relocation with business refactoring.

## 2. Target folder skeleton (illustrative; create only when a use case needs it)

```text
agent-workflow/
├── src/agent_workflow/                     # R2 implementation path; may lose src/ later
│   ├── __init__.py
│   ├── domain/
│   │   ├── shared/{exceptions.py,values.py}
│   │   ├── task/{entities.py,policies.py,exceptions.py}
│   │   ├── workflow/{states.py}           # Canonical WorkflowStatus, if retained as shared context
│   │   ├── review/{entities.py,policies.py,exceptions.py}
│   │   ├── dispatch/{entities.py,policies.py,states.py}
│   │   ├── ci/{evidence.py,policies.py,states.py}
│   │   └── runtime/{values.py,policies.py}
│   ├── application/
│   │   ├── shared/{unit_of_work.py,errors.py}
│   │   ├── task/
│   │   │   ├── ports.py
│   │   │   └── use_cases/{authorize_task.py,register_handoff.py,get_assignment.py}
│   │   ├── review/
│   │   │   ├── ports.py
│   │   │   └── use_cases/{list_queue.py,get_context.py,claim_review.py,
│   │   │                   submit_review.py,get_result.py}
│   │   ├── dispatch/
│   │   │   ├── ports.py
│   │   │   └── use_cases/{dispatch_task.py,reconcile_dispatch.py}
│   │   ├── ci/
│   │   │   ├── ports.py
│   │   │   └── use_cases/{record_ci_evidence.py,reconcile_ci.py}
│   │   └── runtime/
│   │       ├── ports.py
│   │       └── use_cases/{execute_task.py,validate_worker_result.py}
│   ├── infrastructure/
│   │   ├── config/{app_config.py,security_config.py}
│   │   ├── database/
│   │   │   ├── session_factory.py         # Sync + optional async lifecycle
│   │   │   ├── unit_of_work.py            # Transaction boundary
│   │   │   ├── models.py                  # SQLAlchemy metadata / ORM
│   │   │   └── repositories/
│   │   │       ├── sqlalchemy_task_repo.py
│   │   │       ├── sqlalchemy_review_repo.py
│   │   │       ├── sqlalchemy_dispatch_repo.py
│   │   │       └── sqlalchemy_ci_repo.py
│   │   ├── github/{app_client.py,ci_checks.py,webhook_verify.py}
│   │   ├── execution/{docker_backend.py,container_policy.py}
│   │   └── observability/{context.py,json_logger.py,health.py}
│   ├── interfaces/
│   │   ├── mcp/{server.py,tools.py,security.py,auth0_oauth.py,review_request.py}
│   │   ├── webhook/{http.py,handlers.py}
│   │   └── cli/{admin.py,controller.py,ci_watcher.py}
│   ├── composition/
│   │   ├── database.py
│   │   ├── task_deps.py
│   │   ├── review_deps.py
│   │   ├── mcp_deps.py
│   │   ├── dispatch_deps.py
│   │   ├── ci_deps.py
│   │   ├── runtime_deps.py
│   │   └── health_deps.py
│   └── workers/
│       └── trusted_git/
│           ├── main.py                   # Minimal CLI entrypoint
│           ├── prepare.py
│           ├── publish.py
│           ├── git_client.py
│           ├── github_app.py
│           ├── github_client.py
│           ├── snapshot.py
│           └── http_transport.py
├── tests/
│   ├── unit/{domain,application}/
│   ├── architecture/
│   ├── integration/{postgres,mcp,webhook,worker}/
│   ├── security/
│   └── contract/
├── alembic/versions/
├── .github/workflows/v1-ci.yml
├── architecture/policy.toml
└── pyproject.toml
```

The braces group *candidate* files, not a request to add empty placeholders. Existing source, `tests/test_r2_00_characterization.py`, and critical **PostgreSQL/security** tests remain as the behavior baseline until their replacements are verified. Retired SQLite-only tests are not coverage targets; retain tests of active PostgreSQL and security behavior. Do not rename imports, `src/`, `mcp` or the worker executable across the codebase in a single commit.

### Source-module ownership

For the single mandatory R2 DI/layer/file-header convention, see [Engineering Standards](../conventions/engineering-standards.md#r2-layer-di-and-file-header-contract). This blueprint describes the **target**, not a mandatory full-document read for each Coder task.

### Feature ownership / readable entrypoints

| User-visible/business action | Primary future use case | Domain policy | Required ports |
| --- | --- | --- | --- |
| Owner authorizes task | `AuthorizeTaskUseCase` | repository/base/scope/effort invariant | task repository, UoW |
| Coder hands off exact SHA | `RegisterHandoffUseCase` | authorized path, SHA, repair rules | task/review state, UoW, CI tracking |
| TL lists/claims review | `ListReviewQueueUseCase`, `ClaimReviewUseCase` | two-round limit, lease, stale SHA, trusted CI | review repository, UoW, clock, CI evidence |
| TL submits verdict | `SubmitReviewUseCase` | finding uniqueness, prior resolution, verified effort, verdict transitions | review repository, UoW, CI evidence, clock |
| Controller dispatches worker | `DispatchTaskUseCase` | task eligibility, claim fencing, one-slot rule | dispatch store, leadership, runtime port |
| Controller reconciles uncertainty | `ReconcileDispatchUseCase` | unknown external effects cannot be blindly retried | dispatch state, external observation port |
| CI watcher records evidence | `RecordCiEvidenceUseCase` | exact HEAD, trusted check identity, sticky failures | CI store, GitHub checks port |
| Worker executes phases | `ExecuteTaskUseCase` | authorized capability, image pin, deadlines | runtime plugin port, execution backend, evidence |

Example method names are design targets; **do not invent new workflow states, policy semantics, or public APIs** in a rename PR. In particular, published GitHub operations cannot be made atomic with a PostgreSQL transaction.

## 3. Coding patterns (consult actual merged source)

### 3.1 Domain is pure; Application ports are Protocols

Follow the [canonical layer and validation rules](../conventions/engineering-standards.md#r2-layer-di-and-file-header-contract). Domain has pure policies/errors, Application typed inputs/ports; Interfaces validate transport, Infrastructure maps persistent values. Reuse canonical `WorkflowStatus`, `DispatchStatus`, `CiVerdict` in [states.py](../../src/agent_workflow/domain/states.py) for finite states. Preserve exact persisted/wire values; no duplicate/shadow enums, unknown-state defaults or new enums for open identifiers. Use negative tests for new raw state strings, duplicate definitions and invalid states; no false passes for transport or legacy exceptions.

### 3.2 One named Use Case — small and auditable

Use [RegisterTaskHandoffUseCase](../../src/agent_workflow/application/task/use_cases/register_task_handoff.py) and [AuthorizeTaskUseCase](../../src/agent_workflow/application/task/use_cases/authorize_task.py) as merged business transaction examples. Inject existing `IUnitOfWork` and feature repository ports **separately** (not a repository-container UoW). Validate before entering the transaction, execute policy against locked facts, request commit only after successful mutation. Independent reads and atomic persistence writes need no Application UoW. See [three DB patterns](../conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership). CI observation must not cross a race boundary; preserve exact trusted binding, leases, idempotency and concurrency. External effects outside DB with reconciliation.

### 3.3 Composition reads like the demo

See [task dependency assembly](../../src/agent_workflow/composition/task_deps.py), [handoff provider](../../src/agent_workflow/composition/task_handoff_deps.py) and [review reader wiring](../../src/agent_workflow/composition/review_deps.py). Fresh shared Session/UoW/repository per invocation where required; no eager session, hidden I/O, global Session or long-running Worker transaction. Scoped Composition providers own the lifetimes of the resources they open. Composition constructs and injects dependencies. Presentation invokes the use case. Preserve exact MCP/Controller/Worker identity and lifetimes.

### 3.4 PostgreSQL factory, AppConfig and sync/async

- Model `PostgresSessionFactory` on the demo: `sync_engine`/`async_engine` and `get_sync_session()`/`get_async_session()`, lazy where feasible; optional async support is **not permission to convert V1 sync transactions**. A single class can expose both, but engines/pools are separate and only initialized by actual consumers.
- Keep the existing `PostgresFactory` safety: validate DSN and Alembic revision, fail closed, parameter hiding, lock/statement timeouts, correct isolation, short transactions, rollback/close, deadline hooks, durable commits and leadership ownership. Its current `NullPool` and connection-fencing behavior must be characterized before any pool default changes.
- Use a single readable `AppConfig` backed by `pydantic-settings.BaseSettings`, as in demo; a safe `get_app_config()` entrypoint may cache **immutable nonsecret configuration per process**. `.env` is **local development only**. Real deployment loads process env/mounted secrets; do not log/serialize secrets or weaken strict current validators. Separate security-specific settings when that clarifies ownership, but keep one clear entrypoint and preserve exact config semantics until approved cleanup.
- The sync/async UoW implementations must be explicit; two kinds of session **must never share one transaction**. Migrations remain Alembic-owned; the worker does not import ORM/DB drivers.

### 3.5 Demo source reference — patterns to reuse

Reference: [`clean_arch_demo@b274fe4`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145). Codex should inspect only the files relevant to its assigned task:

| R2 concern | Demo source | Required R2 pattern |
| --- | --- | --- |
| Domain + Use Cases | [`src/domain/`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145/src/domain), [`src/application/`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145/src/application) | Pure entities, canonical `StrEnum`, typed DTOs and one named use case per business action |
| Ports + UoW | [`ports.py`](https://github.com/builacviet/clean_arch_demo/blob/b274fe483c88f1524cce0ac275ade266f03ec145/src/application/user/ports.py), [`unit_of_work.py`](https://github.com/builacviet/clean_arch_demo/blob/b274fe483c88f1524cce0ac275ade266f03ec145/src/application/shared/unit_of_work.py) | `typing.Protocol`, one transaction and shared repository session |
| PostgreSQL + AppConfig | [`session_factory.py`](https://github.com/builacviet/clean_arch_demo/blob/b274fe483c88f1524cce0ac275ade266f03ec145/src/infrastructure/database/session_factory.py), [`app_config.py`](https://github.com/builacviet/clean_arch_demo/blob/b274fe483c88f1524cce0ac275ade266f03ec145/src/infrastructure/config/app_config.py) | Readable typed settings, sync/optional async session lifecycle; retain existing Agent Workflow PostgreSQL transaction and security rules |
| Dependency Injection | [`src/presentation/dependencies/`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145/src/presentation/dependencies) | Short `composition/database.py` and `*_deps.py` functions |
| Quality and observability | [`pyproject.toml`](https://github.com/builacviet/clean_arch_demo/blob/b274fe483c88f1524cce0ac275ade266f03ec145/pyproject.toml), [`tests/architecture/`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145/tests/architecture), [`src/infrastructure/observability/`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145/src/infrastructure/observability) | Five CI gates, architecture unit tests, JSON logging and request correlation |

**Keep existing Agent Workflow strengths unchanged:** exact-SHA CI, PostgreSQL locks/fencing/idempotency, secure sandbox, trusted GitHub App boundaries and external side-effect reconciliation. Do not import unrelated demo technologies or spend R2 effort on SQLite.

## 4. Trusted worker and hard boundaries

`workers/trusted_git` is the trusted executable, *not* a plugin placed inside untrusted Code/Test. `prepare.py` and `publish.py` may depend on worker-local Git/GitHub interfaces; `snapshot.py` enforces path/symlink/denylist rules. Do not expose `GitHubApp` or installation tokens via generic runtime ports, logs, mounts or test containers. Keep service/data/credential ownership checks and no workflow DB imports. Any code movement touching credentials, Docker mounting, policy, public entrypoints or write permission is **HIGH/protected**, not an automatic LOW rename.

Runtime adapter contracts stay plug-and-play for approved `Dev/Code` only. Introduce a separate `ExecutionBackend` port; never imply QC/Reviewer/Podman or other roles are implemented merely by adding a directory.

## 5. Historical migration map and PR sequence

The original migration map below records starting locations and planned slices, not current imports or launch commands. MCP migration is complete through #150; remaining entrypoint cleanup follows the five-part ownership above. See [current architecture](../architecture.md) for active debt.

| Slice | Scope and acceptance | Existing implementation to migrate | Gate |
| --- | --- | --- | --- |
| **R2-ARCH-00 Docs** (this PR) | Blueprint + quality/test contract; no runtime/CI edits | ADR-AW-001, tests, policy, demo | Documentation diff only |
| **R2-01 Baseline** | Capture PostgreSQL-first architecture graph, exceptions, core test/type/complexity counts and public DTOs; no silent exemptions | `tests/test_r2_00_characterization.py`, `tests/test_architecture_policy.py`, `architecture/policy.toml` | Baseline report + negative regression fixtures; current CI remains required |
| **R2-02 Domain/Application** | Split task/review/CI policies and named use cases, typed DTOs/ports; legacy facade forwards temporarily | `domain/service.py` (~531 lines), `domain/contracts.py`, `mcp/tools.py` | Unit + characterization + contract tests, remove exact exemptions as fixed |
| **R2-03 DB/Config** | three canonical DB patterns; typed persistence; sync first, optional async only with consumer; safe config consolidation | `storage/postgres_*.py`, `infrastructure/database/`, `app_config.py`, `composition/postgres_*.py` | PostgreSQL concurrency, Alembic, rollback, scope/lifetime, failures |
| **R2-04 Dispatch/Runtime** | Extract dispatch/reconcile use cases, backend/plugin interfaces; simplify trusted worker | `orchestration/*`, `runners/*`, `workers/trusted_git/*` | Sandbox, credential isolation, uncertain publish, leadership, pinned image tests |
| **R2-05 Interfaces/Observability** | Move inbound MCP/webhook/CLI adapters under `interfaces/`; one `composition/`; JSON logging and service-scoped health | `mcp/*`, `bootstrap/*`, `integrations/webhook_http.py` | Auth0, webhook signature, secret-safe logs, correlation, health failure tests |
| **R2-06 Quality enforcement** | Independently enable full mandatory quality gates on the completed PostgreSQL-first package; retire only remaining temporary facades after consumers migrate | `.github/workflows/v1-ci.yml`, `pyproject.toml`, architecture/security tests | All five new gates plus existing exact-SHA, PG and sandbox gates |

Slices are an ordering proposal, **not preauthorization of protected files**. Each source/CI/policy/schema change requires a specific approved Notion task (PRIMARY/RELATED/PROTECTED envelope), one Draft PR, exact-HEAD CI and independent TL review; **Owner alone merges**. Never modify CI/policy and use that same PR's green checks as independent evidence of gate integrity.

## 6. Acceptance criteria for the completed R2 program

1. Every core business action has an obvious `application/<feature>/use_cases/<verb>.py`, tests, typed input/output and an explicit dependency map. No application/domain import of concrete persistence or runtime transport.
2. `Domain` has only pure invariants/entities/state transitions; old exact import debt and runner cycle are retired with no wildcard exemptions.
3. PostgreSQL factory/UoW/config meet readability goals and preserve lock, lease, fencing, exact-SHA evidence, idempotency, rollback and migration compatibility. Sync supported; async tested before any real consumer uses it.
4. MCP/OAuth/webhook/CLI/Controller/Worker behavior, entrypoints and serialized contracts remain compatible until explicitly changed and independently verified. Only the Owner may authorize deployment/cutover.
5. Trusted worker never accesses workflow DB and credential-free Code/Test never receives GitHub App secrets; external effects are not falsely claimed atomic with DB.
6. JSON logs with validated correlation IDs, bounded per-service liveness/readiness and negative tests exist; no sensitive payload/credential logging.
7. R2 quality contract gates are green on the reviewed PR HEAD with truthful PostgreSQL-first core coverage/type/import/migration evidence; removed SQLite modules and their retired tests are not included in the new 90% core target. No blanket excludes, fabricated passes or bypasses.
8. The final package layout remains a single `agent_workflow` namespace. No `agent_workflow_v2`, duplicate source trees, or second deployed stack.

**Non-goals:** Investment in obsolete SQLite feature parity, new SQLite unit tests/coverage, migration scaffolding merely to preserve SQLite; microservices, RabbitMQ/Celery copy from demo, artificial interface-per-class, runtime/provider expansion beyond approved V1, host execution for untrusted worker, production cutover and root-package move in the same PR as a use-case refactor.
