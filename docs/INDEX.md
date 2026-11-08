# Documentation map — agent fast start

**Purpose:** Find enough trustworthy project context for one authorized task without loading the whole repository. This index is a *router*, not a substitute for reading the relevant contract, source code and tests. Last reconciled for merged baseline: 2026-11-07, after DOC-02 [PR #16](https://github.com/builacviet/agent-workflow/pull/16) (merge `2048ea9383509bf389aff6bdb2a3f02fb2e922ef`). Later documentation proposals are not assumed merged.

**V1 delivery route (Owner approval pending):** [V1 scope and gate contract](v1-scope-and-gates.md) is the proposed implementation authority for V1 after the V1-00 Owner-approved merge. It takes precedence over CL-12/M01 *target implementation decisions* for V1 only; do not infer that V1 runtime gates are already implemented.

## First read — every agent

1. [`AGENTS.md`](../AGENTS.md): mandatory scope, safety, roles, review rules.
2. **This index:** locate the *current* behavior and the *target* design relevant to the task.
3. **The currently authorized task and acceptance criteria** (Notion task, linked `docs/tasks/` file, or explicit Owner instruction). Verify exact branch/base/head SHAs; do not assume a historical task is active.
4. Inspect only the affected source modules, entrypoints, tests and interface contracts. Expand references **on demand**. Never read every ADR or historical handoff by default.

## Three meanings of "architecture" — do not confuse

| Class | Source of truth | Interpretation |
| --- | --- | --- |
| **CURRENT (as-built)** | [`architecture.md`](architecture.md), [Phase 3.1 runbook](phase-3.1.md), relevant code/tests | What the repository currently implements; deployments need separate verification |
| **TARGET (Owner-selected development design)** | [CL-12 consolidated ADR](adr/CL-12-architecture.md), with [Executor security](adr/CL-12-executor-security.md) and [deployment](adr/CL-12-deployment.md) | Rootless Podman Linux-host Executor, authenticated typed IPC, ephemeral Prepare/Coder/Test/Publish, GHCR digest cache and staged observability. **Not implemented or production-approved by merging an ADR** |
| **EVIDENCE / HISTORY** | [ADR inventory](adr/CL-12-current-state.md), [historical handoffs](history/handoff-log.md), [old workflow notes](poc-workflow.md), reviewed PRs | Dated snapshots, decisions, limitations; not automatically today's running state |

**As of 2026-11-07:** CL-12.5 [PR #11](https://github.com/builacviet/agent-workflow/pull/11), CL-12.6 [PR #12](https://github.com/builacviet/agent-workflow/pull/12) and CL-12.7 [PR #13](https://github.com/builacviet/agent-workflow/pull/13) are **merged as documentation**. Controller→Executor IPC transport and crash/job idempotency are *pending implementation/test*, not settled runtime facts. P1 [PR #14](https://github.com/builacviet/agent-workflow/pull/14) is a 2026-11-06 **BLOCKED read-only preflight**; later WSL2 P2/P3 are limited feasibility reports, with P3 source evidence review outstanding. Before making a time-sensitive claim, re-check GitHub and the active Notion status.

## Read by task type (pick a route, not the whole tree)

| Task / role | Start with, after the first-read trio |
| --- | --- |
| **V1 planning / Dev / QC / TL Reviewer / Owner gates** | [V1 scope and gate contract](v1-scope-and-gates.md) first, then the authorized Notion task and exact source/test/CI evidence; do not use older POC role assignments as V1 authority |
| Small `order_service` logic | [CURRENT architecture](architecture.md), relevant `src/order_service/` module and adjacent tests; review policy only for submission |
| Existing WFC/MCP/Controller bug or operation | Start at the [CURRENT Phase 3.1 runbook](phase-3.1.md) and only the affected source/tests; [my-wfc.md](my-wfc.md) contains **historical pre-Phase-3.1 assumptions** and is reference-only for older WFC behavior |
| **Controller timeout / Docker CLI disconnect / uncertain GitHub publish** | Go **directly** to the [CURRENT dispatch/recovery evidence + source/test map](guides/dispatch-recovery.md), then [Phase 3.1 runbook](phase-3.1.md) and selected code/test symbols; do **not** load the full ADR set by default |
| Auth0/OAuth | [Active OAuth runbook](auth0-oauth.md); [superseded notes](oauth-auth0.md) **only** for historical context |
| SQLite/schema/migration | [CL-12 persistence ADR](adr/CL-12-persistence.md), [consolidated ADR](adr/CL-12-architecture.md) relevant sections, live source/tests; do not infer migration is deployed |
| Executor / worker security / Podman | [CL-12 consolidated ADR](adr/CL-12-architecture.md), [Executor security](adr/CL-12-executor-security.md), [deployment](adr/CL-12-deployment.md), applicable approved task and negative-test plan |
| CI images / GHCR / telemetry | [Deployment ADR §§9–10](adr/CL-12-deployment.md), [consolidated ADR](adr/CL-12-architecture.md); future implementation requires explicit authorization |
| Manual local development / Codex task setup | [Local worktree guide](guides/local-worktrees.md) for branch, base, path and handoff checks |
| Tech Lead review or Coder handoff | [Review policy](review-policy.md), [short handoff template](templates/handoff.md), exact PR diff/AC and actual test results; history only if comparison matters |
| Architecture change / cross-module refactor | [Consolidated ADR](adr/CL-12-architecture.md), [module boundaries](adr/CL-12-module-boundaries.md), [current-state inventory](adr/CL-12-current-state.md) only where needed |
| Deployment / migration operations | [Current Phase 3.1 runbook](phase-3.1.md), [Docker MCP POC](docker-mcp.md) as applicable, then target [deployment ADR](adr/CL-12-deployment.md) **as proposal**, not run instructions |

## Navigation by purpose

- **V1 delivery:** [V1 scope and gates](v1-scope-and-gates.md) → Owner-approved planning package / task AC → exact-HEAD evidence → per-PR Owner merge gate.
- **Contracts:** [`AGENTS.md`](../AGENTS.md) → [review policy](review-policy.md) → [handoff template](templates/handoff.md) → relevant authorized task (for example, [CL-12](tasks/CL-12.md)).
- **As-built:** [architecture](architecture.md) → [Phase 3.1 CURRENT runbook](phase-3.1.md) → [scenario-specific source/test map](guides/dispatch-recovery.md) when relevant. [my-wfc](my-wfc.md) is partly historical, not the default dispatch authority.
- **Target:** [CL-12 summary](adr/CL-12-architecture.md) → open the exact linked ADR section relevant to the current task.
- **Historical:** [old handoff log](history/handoff-log.md), [migration notes](migration-my-wfc.md), [POC workflow](poc-workflow.md), [historical TASK-001](tasks/TASK-001.md). These are not general startup reading.

## Token and evidence discipline

- Read **headings/index/required section first**, expand only referenced paragraphs; prefer file/symbol searches and bounded diffs over entire history logs. **Search the Git tree/path list first** when symbol search is missing or non-indexed; use exact file paths from this index, not speculative filenames. For GitHub `fetch_file`, request bounded `start_line/end_line` for known symbols; distinguish lines returned by a tool from lines actually placed in context. Avoid re-fetching full files just to count them.
- A task is not understood until its **AC, current call path, trust boundary, affected tests and forbidden scope** are identified; do not sacrifice correctness for a token quota.
- Distinguish **observed by tests**, **reported by another agent**, **Owner-selected design**, and **unverified/proposed**. Link claims to file, commit or dated evidence; never claim a design is deployed.
- If this index and a merged ADR conflict, the **merged ADR governs its own target design**; for an **Owner-approved V1 task**, [the V1 scope contract](v1-scope-and-gates.md) governs V1 implementation choices where CL-12/M01 target recommendations conflict. Current source/tests and confirmed deployment evidence still govern current behavior. **Status provenance:** the [Executor security ADR](adr/CL-12-executor-security.md) now distinguishes merged design documentation (PRs #11/#12/#13) from separate CL-12 parent sign-off, implementation approval and production gates. Escalate true contradictions; do not silently pick a convenient version.
- Changes to an ADR or an agent entrypoint should update this index or explicitly state why not. Never compress away audit, permission or safety gates.
