# Documentation map — agent fast start

**R0 architecture governance proposal (pending Owner merge):** [Canonical as-built and target routing](architecture.md) → [ADR-AW-001](adr/AW-001-clean-architecture-governance.md) → [AST import policy](../architecture/policy.toml) → [Worker Adapter contract](architecture/worker-runtime-plugins.md). The Owner has selected PostgreSQL as the target; older CL-12 SQLite/Compose Controller/Podman target assumptions are historical, not current production instructions. Exact merged code and runtime evidence still take precedence.

**Purpose:** Find enough trustworthy project context for one authorized task without loading the whole repository. This index is a *router*, not a substitute for reading the relevant contract, source code and tests. Last reconciled for merged baseline: 2026-11-07, after DOC-02 [PR #16](https://github.com/builacviet/agent-workflow/pull/16) (merge `2048ea9383509bf389aff6bdb2a3f02fb2e922ef`). Later documentation proposals are not assumed merged.

**V1 delivery route (Owner-approved in PR #22):** [V1 scope and gate contract](v1-scope-and-gates.md) is the implementation scope authority for V1. It takes precedence over CL-12/M01 *target implementation decisions* for V1 only; it does **not** imply that V1 runtime gates are already implemented. Executor/Podman are not part of V1.

**Start with diagrams:** [V1 architecture + planning/execution Mermaid flows](v1-visual-flows.md). These are design diagrams, not deployed-runtime evidence. Intended Controller host: **Ubuntu on WSL2**, subject to V1-01 readiness checks.

## First read — every agent

1. [`AGENTS.md`](../AGENTS.md): mandatory scope, safety, roles, review rules.
2. **This index:** locate the *current* behavior and the *target* design relevant to the task.
3. **The currently authorized task and acceptance criteria** (Notion task, linked `docs/tasks/` file, or explicit Owner instruction). Verify exact branch/base/head SHAs; do not assume a historical task is active.
4. Inspect only the affected source modules, entrypoints, tests and interface contracts. Expand references **on demand**. Never read every ADR or historical handoff by default.

## Three meanings of "architecture" — do not confuse

| Class | Source of truth | Interpretation |
| --- | --- | --- |
| **CURRENT (as-built)** | [`architecture.md`](architecture.md), [Phase 3.1 runbook](phase-3.1.md), relevant code/tests | What the repository currently implements; deployments need separate verification |
| **TARGET (proposed new governance, pending Owner approval)** | [ADR-AW-001](adr/AW-001-clean-architecture-governance.md), [Worker adapters](architecture/worker-runtime-plugins.md) | PostgreSQL-backed modular monolith, host-systemd Controller/CI watcher, Compose ingress/DB, approved plugin ports. Not deployed. |
| **EVIDENCE / HISTORY** | [ADR inventory](adr/CL-12-current-state.md), [historical handoffs](history/handoff-log.md), [old workflow notes](poc-workflow.md), reviewed PRs | Dated snapshots, decisions, limitations; not automatically today's running state |

**As of 2026-11-07:** CL-12.5 [PR #11](https://github.com/builacviet/agent-workflow/pull/11), CL-12.6 [PR #12](https://github.com/builacviet/agent-workflow/pull/12) and CL-12.7 [PR #13](https://github.com/builacviet/agent-workflow/pull/13) are **merged as documentation**. Controller→Executor IPC transport and crash/job idempotency are *pending implementation/test*, not settled runtime facts. P1 [PR #14](https://github.com/builacviet/agent-workflow/pull/14) is a 2026-11-06 **BLOCKED read-only preflight**; later WSL2 P2/P3 are limited feasibility reports, with P3 source evidence review outstanding. Before making a time-sensitive claim, re-check GitHub and the active Notion status.

## Read by task type (pick a route, not the whole tree)

| Task / role | Start with, after the first-read trio |
| --- | --- |
| **V1 planning / Dev / QC / TL Reviewer / Owner gates** | Read [V1 scope and gates](v1-scope-and-gates.md), the shared [Engineering Quality Standards](conventions/engineering-standards.md), then [Dev contract](roles/dev.md), [QC contract](roles/qc.md), [Independent TL Reviewer contract](roles/tl-reviewer.md). For `qc: required`, use the [MTP design template](templates/master-test-plan.md) and separate [MTP execution-report template](templates/mtp-execution-report.md). These are **docs-only contracts**, not proof of automated role enforcement (V1-02 pending). Next read authorized task and exact source/CI evidence. |
| Small `order_service` logic | [CURRENT architecture](architecture.md), relevant `src/order_service/` module and adjacent tests; review policy only for submission |
| Existing WFC/MCP/Controller bug or operation | Start at the [CURRENT Phase 3.1 runbook](phase-3.1.md) and only the affected source/tests; [my-wfc.md](my-wfc.md) contains **historical pre-Phase-3.1 assumptions** and is reference-only for older WFC behavior |
| **Controller timeout / Docker CLI disconnect / uncertain GitHub publish** | Go **directly** to the [CURRENT dispatch/recovery evidence + source/test map](guides/dispatch-recovery.md), then [Phase 3.1 runbook](phase-3.1.md) and selected code/test symbols; do **not** load the full ADR set by default |
| Auth0/OAuth | [Active OAuth runbook](auth0-oauth.md); [superseded notes](oauth-auth0.md) **only** for historical context |
| SQLite/schema/migration | [Current code + target route](architecture.md), [proposed PostgreSQL ADR-AW-001](adr/AW-001-clean-architecture-governance.md), actual storage tests; older [SQLite ADR](adr/CL-12-persistence.md) is historical |
| Executor / worker security / Podman | [CL-12 consolidated ADR](adr/CL-12-architecture.md), [Executor security](adr/CL-12-executor-security.md), [deployment](adr/CL-12-deployment.md), applicable approved task and negative-test plan |
| CI images / GHCR / telemetry | [Deployment ADR §§9–10](adr/CL-12-deployment.md), [consolidated ADR](adr/CL-12-architecture.md); future implementation requires explicit authorization |
| Manual local development / Codex task setup | [Local worktree guide](guides/local-worktrees.md) for branch, base, path and handoff checks |
| Tech Lead review or Coder handoff | [Review policy](review-policy.md), [short handoff template](templates/handoff.md), exact PR diff/AC and actual test results; history only if comparison matters |
| Architecture change / cross-module refactor | [Canonical architecture](architecture.md), [proposed ADR-AW-001](adr/AW-001-clean-architecture-governance.md), [import policy](../architecture/policy.toml), [Agent Task Contract](templates/agent-task-contract.md); historical CL-12 ADRs only if needed |
| Deployment / migration operations | [Current Phase 3.1 runbook](phase-3.1.md), [V1-03C local integration](guides/v1-03c-local-webhook.md); [retired Docker MCP POC](docker-mcp.md) is history only, then target [deployment ADR](adr/CL-12-deployment.md) **as proposal**, not run instructions |

## Navigation by purpose

- **V1 delivery:** [V1 scope and gates](v1-scope-and-gates.md) → Owner-approved planning package / task AC → exact-HEAD evidence → per-PR Owner merge gate.
- **Contracts:** [`AGENTS.md`](../AGENTS.md) → [review policy](review-policy.md) → [handoff template](templates/handoff.md) → relevant authorized task (for example, [CL-12](tasks/CL-12.md)).
- **As-built:** [architecture](architecture.md) → [Phase 3.1 CURRENT runbook](phase-3.1.md) → [scenario-specific source/test map](guides/dispatch-recovery.md) when relevant. [my-wfc](my-wfc.md) is partly historical, not the default dispatch authority.
- **Target:** [proposed AW-001](adr/AW-001-clean-architecture-governance.md) → approved task; older CL-12 decisions are historical/superseded for PostgreSQL and runtime placement.
- **Historical:** [old handoff log](history/handoff-log.md), [migration notes](migration-my-wfc.md), [POC workflow](poc-workflow.md), [historical TASK-001](tasks/TASK-001.md). These are not general startup reading.

## Token and evidence discipline

- Read **headings/index/required section first**, expand only referenced paragraphs; prefer file/symbol searches and bounded diffs over entire history logs. **Search the Git tree/path list first** when symbol search is missing or non-indexed; use exact file paths from this index, not speculative filenames. For GitHub `fetch_file`, request bounded `start_line/end_line` for known symbols; distinguish lines returned by a tool from lines actually placed in context. Avoid re-fetching full files just to count them.
- A task is not understood until its **AC, current call path, trust boundary, affected tests and forbidden scope** are identified; do not sacrifice correctness for a token quota.
- Distinguish **observed by tests**, **reported by another agent**, **Owner-selected design**, and **unverified/proposed**. Link claims to file, commit or dated evidence; never claim a design is deployed.
- If this index and a merged ADR conflict, the **merged ADR governs its own target design**; for an **Owner-approved V1 task**, [the V1 scope contract](v1-scope-and-gates.md) governs V1 implementation choices where CL-12/M01 target recommendations conflict. Current source/tests and confirmed deployment evidence still govern current behavior. **Status provenance:** the [Executor security ADR](adr/CL-12-executor-security.md) now distinguishes merged design documentation (PRs #11/#12/#13) from separate CL-12 parent sign-off, implementation approval and production gates. Escalate true contradictions; do not silently pick a convenient version.
- Changes to an ADR or an agent entrypoint should update this index or explicitly state why not. Never compress away audit, permission or safety gates.
