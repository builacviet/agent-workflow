# Agent Workflow V1 — Scope and Gate Contract

> **Status:** Proposed for Owner approval in V1-00. This document becomes the **V1 delivery scope authority only after its Draft PR is approved and merged by the Owner**. Its existence does not grant deployment or task-execution approval.
>
> **Baseline:** `main` at `4a23d5dfac28c1f8ee71187fe00cac06e880d4d1` (following PR #21). [V1-00 acceptance criteria and planning draft](https://app.notion.com/p/412f92014abd81528012fd5ea320cde3).

## 1. Scope and precedence

V1 delivers the smallest sequential, approval-gated workflow using the existing Phase 3.1 architecture, not a new orchestration platform.

- **In scope:** ChatGPT TL planning; independent Codex CLI Dev, QC, and TL Reviewer roles; role-specific prompt/permissions and model/reasoning-effort from deployment environment; trusted Controller/Worker; existing Docker CLI, SQLite/WFC and GitHub App; GitHub Actions CI; Telegram notifications/identity-gated human approvals.
- **Deployment intent:** Keep the host Controller and Docker-CLI architecture; run the Controller under Linux systemd via the separately authorized V1 operations work. Current host engine authority is broad and risky; neither systemd operation nor production hardening is proven by this document.
- **Compatibility:** Preserve Streamable HTTP `/mcp` and the **nine existing MCP tools**, including names, signatures, response contracts and authentication behavior. New functionality may only be added compatibly through separately approved tasks.
- **Out of scope:** new Executor service, Podman/Controller-to-Executor IPC, GHCR or Grafana rollout, epic branches, concurrent/parallel task execution, auto-merge, new orchestration framework, recurring ChatGPT scheduler, and guaranteed ChatGPT-native mobile approval/notification.
- **Architecture override:** For **Owner-approved V1 delivery only**, this contract takes precedence over CL-12/M01 **target implementation** recommendations where they conflict with the above V1 scope. CL-12/M01 ADRs and their historical evidence remain intact and are not rewritten, revoked as history, or evidence of deployed runtime. Current source/tests and verified deployment evidence still govern **as-built** claims; security and least-privilege constraints remain applicable.

## 2. Planning gates — once per approved package

1. **TL Planner (ChatGPT):** Produce a versioned Plan, atomic tasks, testable acceptance criteria (AC), dependencies, changed-path scope, and `qc: required | skip` **with a reason** for each task.
2. **QC Codex (conditional):** For each `qc: required` task, produce an AC-traceable Master Test Plan (MTP) **before Dev**. For `qc: skip`, no MTP is required.
3. **Independent TL Reviewer Codex:** Review the Plan/AC and any MTP. Route deficient artifacts back to their author, version corrections, and keep revision attempts bounded; unresolved plan defects are escalated rather than retried indefinitely.
4. **Owner:** Explicitly approve **one complete reviewed planning package**, identified by an immutable version/hash. Do not dispatch any Dev task until approved. Any substantive change to approved Plan/AC/MTP invalidates approval and requires renewed review/Owner authorization.

The Owner's planning-package approval does **not** pre-authorize merging subsequent PRs.

## 3. Execution gates — strictly one atomic task at a time

1. **Authorized dispatch:** Controller selects only the next task in the approved order, with exact approved AC, allowed paths, model/effort policy and task/base SHA. No parallel execution.
2. **Dev:** Codex CLI edits only its scoped, credential-free task snapshot. In the automated flow it does **not** receive GitHub credentials, active WFC data, engine access or Owner approval authority.
3. **Publish:** Trusted Worker alone commits/pushes the task branch and opens/updates a **Draft PR** to `main`; record exact PR URL and latest HEAD SHA.
4. **CI gate:** GitHub Actions must succeed for the **exact current PR HEAD**. Absent, pending, failed, or stale checks block progress. The V1-03 CI implementation is future work; this document does not claim the gate already runs.
5. **Code gate:** Independent **Codex TL Reviewer** examines the exact diff, AC, security boundaries and CI evidence. Findings go back to Dev. A changed HEAD invalidates previous CI/review evidence and must be revalidated. Allow **at most two code-review rounds**; a second failed code review is `ESCALATED`, not a silent third round.
6. **QC gate (conditional):** When `qc: required`, independent QC executes the **preapproved MTP** against the reviewed, exact HEAD, records artifacts, and independent TL Reviewer accepts or rejects the QC evidence. When `qc: skip`, skip **only** the QC-specific gates, not CI, code review or Owner approval.
7. **Owner merge gate:** Present PR URL, exact latest HEAD SHA, CI/TL/QC evidence and decision. The Owner explicitly approves **that PR merge**, separately from planning approval. **The Controller and agents never auto-merge.**
8. **Merge reconciliation:** After an Owner-performed merge, Controller verifies from GitHub that the authorized PR actually merged into `main`, records merge SHA and reconciles state before dispatching the next task. Approval alone is not proof of merge.

## 4. Failure, security and audit invariants

- **`BLOCKED`:** operational uncertainty (network/timeouts, Docker issues, missing checks, uncertain publish, inconsistent GitHub/SQLite state). Do not count it as a code-review failure or automatically retry risky effects.
- **`ESCALATED`:** second failed code-review round, or unresolved planning/review inconsistency requiring explicit human decision. Never exceed the two-round code-review cap.
- Bind planning approvals to immutable package version/hash and code/CI/QC verdicts to exact commit SHA. Stale SHA, missing evidence or unverified model/effort where required must fail closed.
- Keep GitHub App keys, tokens, MCP auth secrets, active SQLite/WFC DB and arbitrary host/engine control out of untrusted agents. Reusing the current **trusted host Controller → Docker CLI** is a known, narrowly scoped V1 architectural choice, not proof of least-privilege engine isolation or authorization to broaden that access.
- Record outcomes and provenance (plan version, task, base/head/merge SHA, reviewer, review round, AC, CI/QC evidence, Owner decision). Do not misstate target design as observed implementation.

## 5. V1-00 acceptance and handoff

- This file captures V1 scope, precedence and planning/execution/failure gates. `AGENTS.md` and `docs/INDEX.md` route V1 work here while CL-12/M01 history is preserved.
- V1-00 is **docs-only**: no code, MCP contracts, database schemas, runtime/config, or deployment change. Documentation review and Owner merge approval are required; V1-00 is not a claim that future CI/controller gates are implemented.
- V1-01 (environment/credentials readiness) is the next backlog task **only after Owner approval and confirmed V1-00 merge**. No credential provisioning, production change or live Controller mutation is authorized by V1-00.
