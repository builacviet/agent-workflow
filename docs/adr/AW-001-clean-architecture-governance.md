# ADR-AW-001 — Clean Architecture and Multi-Agent Governance

**Status:** PROPOSED FOR OWNER MERGE — design direction accepted 2026-11-11; this PR is the technical approval gate.  
**Baseline:** main@81f9bf10d3bcb2752cd6010f8821ae7c7872c87d  
**Scope:** R0 architecture/governance only, no functional refactor or deployment.

## Context and decision

Agent Workflow's existing modularization does not enforce dependency inversion. A domain-named module constructs SQLite, storage imports integration policy, and the runner has an import cycle. Functional CI cannot prevent 5–10 agents from independently reintroducing these problems.

Adopt **pragmatic Clean Architecture within a modular monolith**. The conceptual dependency direction is Domain <- Application/Ports <- Adapters/Entrypoints, with composition roots constructing concrete dependencies. Modules are organized around workflow, review, dispatch, CI, execution and reconciliation use cases. An import policy with exact legacy exceptions must be enforced in CI before source moves.

Do not introduce microservices, a universal repository, a framework-driven DI container or arbitrary shared utilities. Shared contracts change only under explicit Tech Lead and Owner approval.

## Target ownership and import boundaries

| Boundary | Owns | May depend on |
| --- | --- | --- |
| Domain | State transitions, immutable values, validation | Pure Python/value types only |
| Application | Use cases, transaction intent, authorization decisions, ports | Domain and pure ports |
| Infrastructure | PostgreSQL repositories, GitHub/HTTP adapters, Docker backend | Application ports, domain |
| Interfaces | MCP/webhook/CLI request handling and transport validation | Application and contracts |
| Bootstrap | Configuration, dependency composition, entrypoint lifecycle | Concrete adapters and upper layers |
| coder_worker | Trusted Prepare/Publish Git/GitHub and narrow execution contract | Own package, approved minimal pure contracts |
| Worker Runtime Adapter | Manifest/capabilities, approved command plan, untrusted output decoding | Runtime port, not workflow DB or secret authority |
| Execution Backend Adapter | Ephemeral container execution, independently attested image, cleanup | Runtime execution port, never business rules |

For existing packages storage/integrations/runners/mcp/orchestration, the checked-in architecture/policy.toml records exact forbidden edges that existed at baseline. The debt inventory is **not approval to add more**. Every R2 refactor must reduce that inventory; removals update the policy in the same PR.

### Plug-and-play is a mandatory design invariant

The already implemented AgentRuntimePlugin, RuntimeManifest and RuntimeRegistry support runtime selection, capability checks and approved image pinning **for Dev/Code only**. They do not yet provide general role/phase plugins or an interchangeable Docker/Podman execution backend. R2 must move the stable port toward Application, keeping legacy entrypoints and tests, and separately define an ExecutionBackend port. No mandatory Executor/Podman expansion in V1. See [worker runtime contract](../architecture/worker-runtime-plugins.md).

**No provider change may require a domain rule change.** Adapter registration, security review and versioned contracts are still required. The trusted Prepare/Publish worker is a separate security authority, not an arbitrary agent plugin.

## PostgreSQL and operational target

PostgreSQL is the Owner-selected authoritative workflow database. SQLite remains a migration source/rollback-window artifact only. Use short transactions, constraints, row locks, leases, fencing generations, idempotency and explicit reconciliation. Advisory locks do **not** fence GitHub/Docker effects and must not be the workflow correctness mechanism. Database and external effects never share a claimed atomic transaction.

Trusted host Dev Controller and CI watcher run as separate Linux/systemd processes; MCP/webhook/nginx/PostgreSQL are Compose services. ngrok is development-only; Worker phases are ephemeral engine-created containers. Multiple Controllers may be permitted for recovery, but V1 retains one dispatch slot until tested. Podman Executor, automated QC/Reviewer, UI and monitoring remain optional future scope.

## Agent contract and architecture gates

Every task needs Owner/Tech Lead approval, one task branch/worktree, owned modules, exact base SHA, allowed file paths, acceptance criteria, test contract and forbidden changes. Shared contract, schema, credential boundary, policy, or manifest changes require coordinated review. Agents cannot modify this policy to bypass checks, auto-merge or attest their own runtime security evidence.

The R0 CI gate uses Python AST to inspect source imports, rejects new cross-layer imports, forbids SQL/network drivers in pure core, forbids DB drivers in trusted Worker and records the existing runner import cycle exactly. Legacy exceptions must be deleted as code is fixed.

**R0 limitation:** Source-text/dynamic imports and semantic coupling cannot all be verified by AST. Automatic PR changed-path authorization against signed task contracts is not yet implemented; review it manually until a separately tested trusted CI checker exists. Passing import tests does not prove isolation, correct transactions or runtime deployment.

## Migration milestones

1. R0: this ADR, canonical architecture docs, exact import policy and task contract.
2. R1: fix CI repair dead-end, enforce CI gate in trusted operational configs, separate sandbox state from DB identifier.
3. R2: characterization tests, split domain/application/ports, composition roots, remove runner cycle; keep MCP/CLI contracts stable.
4. R3–R4: PostgreSQL schema/migration runner, real-role adapter tests, atomic local settlement, leases/fencing and publication reconciliation.
5. R5: Compose/systemd assets, crash/restart and backup/restore evidence, quiesced verified SQLite cutover, Owner-approved activation.
6. R6: retire order_service and compatibility shims **only after** usage/dependency checks and explicit Owner approval.

Existing PR #38/#39 remain Draft and are not merge-ready as-is; reuse only validated changes after rebase, not a blind cherry-pick.

## Review and reversal

No source reorganization, production cutover, Kubernetes, Podman migration or Owner merge is authorized by creating this Draft PR. In each migration PR, preserve tests and existing MCP, OAuth, webhook, GitHub App, CLI and persisted-state compatibility. A rollback should restore prior behavior without replaying ambiguous publication. PR merge requires independent review and Owner decision.
