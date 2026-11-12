# Agent Workflow architecture — canonical entrypoint

**CURRENT source baseline:** main@81f9bf10d3bcb2752cd6010f8821ae7c7872c87d (merged PR #37, 2026-11-11). This is a source fact, **not** proof of production deployment. Recheck GitHub before treating it as current.

**TARGET (PROPOSED, pending Owner PR approval):** [ADR-AW-001 Clean Architecture + Multi-Agent Governance](adr/AW-001-clean-architecture-governance.md). The [machine-readable import/debt policy](../architecture/policy.toml), [Worker Adapter contract](architecture/worker-runtime-plugins.md) and [Agent Task Contract](templates/agent-task-contract.md) define the R0 governance proposal.

## CURRENT — as-built

| Package/process | Actual responsibility and known limitations |
| --- | --- |
| agent_workflow/domain | Public workflow contracts; service.py still combines application use cases and SQLite default composition |
| agent_workflow/storage | SQLite workflow, dispatch, CI and webhook ledgers/migrations; adapters import concrete service/integration in places |
| agent_workflow/integrations | GitHub identity, read-only Controller GitHub App Checks, CI coordination and signed webhook HTTP handling |
| agent_workflow/mcp | MCP server and nine tools (7 workflow + ping/info), Auth0 OAuth and legacy Bearer options |
| agent_workflow/orchestration | Trusted host Dev Controller and independent CI-only watcher CLI |
| agent_workflow/runners | Docker phase coordination, sandbox/watchdog, Codex and approved Dev/Code runtime plugin selection |
| coder_worker | Trusted worker for Prepare/Publish, separate GitHub App credentials and Git operations |
| my_wfc and flat agent_workflow wrappers | Legacy CLI/import compatibility, not a new architectural layer |
| order_service | Independent in-memory POC sample; removal requires dependency check |

**CURRENT Compose:** mcp, webhook, nginx router, optional ngrok tunnel; shared SQLite persistence. No merged Controller or PostgreSQL Compose service. Controllers run as host CLIs; no deployed systemd unit files are claimed. Worker stages (Prepare → Code → Test → Publish) are ephemeral Docker containers; Code/Test are credential-free.

PR #37 merged CI/webhook wiring. Local supervised smoke previously checked OAuth discovery, anonymous MCP denial, signed/unsigned webhook responses and read-only GitHub App Checks. Full trusted worker → CI tracking → Controller verdict → review E2E remains deferred and no production stack is proven. Exact-SHA GitHub Actions tests are **test evidence**, not deployment evidence.

**Known correctness debt:** R1-01 uses immutable Pydantic `AppConfig.from_env()` to validate `WFC_CI_GATE_REQUIRED` (`0`/`1`, default `1`) at MCP/Controller composition boundaries. An explicit `0` bypasses CI for an isolated local test but must never be used with real GitHub publishing or public MCP deployments. `WorkflowService` does not read ENV; direct callers default to requiring CI. READY_IN_REVIEW still rejects a different repair HEAD even after trusted CI failure; Controller/Worker external publication cannot be atomically committed with the DB. Source modules have cross-layer dependencies and one runner import cycle. See Kimi's read-only audit and validate against source/HEAD before treating findings as fixed.

## TARGET — subject to ADR and stage-specific acceptance

- PostgreSQL-backed modular monolith with Domain, Application/Ports, Infrastructure Adapters, Interfaces and composition roots.
- Linux/systemd trusted Controller and CI watcher; Docker Compose MCP/webhook/router/PostgreSQL; ephemeral Docker Worker stages.
- PostgreSQL row locks, uniqueness, leases, fencing, idempotency, explicit publication intent/reconciliation; one V1 dispatch slot.
- Plug-and-play Worker Runtime adapters with approved versioned manifests and a **separate** execution backend abstraction; future Podman/other roles are optional, not implemented.
- Mandatory import-boundary CI, scoped task contracts and separate Owner gate for contracts, schemas and credential boundaries.

## Documentation precedence

- CURRENT: source/merged commit and independently verified runtime evidence, then this overview and [Phase 3.1 runbook](phase-3.1.md) for applicable historical operations.
- Approved V1 scope: [V1 scope and gates](v1-scope-and-gates.md) and the task's Owner approval.
- Proposed target: ADR-AW-001 **only after Owner merge/acceptance**; it does not authorize a production cutover.
- HISTORICAL: CL-12 target design for SQLite/Compose Controller/Podman Executor, including [CL-12 architecture](adr/CL-12-architecture.md), [deployment](adr/CL-12-deployment.md) and [persistence](adr/CL-12-persistence.md), now superseded in the proposed target by Owner's PostgreSQL decision. Historical ADRs are not erased.
- Legacy POC references: [my-wfc](my-wfc.md), [order_service](../src/order_service/) and [historical notes](migration-my-wfc.md). Do not read them for new-workflow architectural authority.
