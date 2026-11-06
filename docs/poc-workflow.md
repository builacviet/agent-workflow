> Current Phase 3.1 architecture supersedes remote verification/gh setup below: WFC is local-only, worker owns GitHub, Tech Lead verifies via plugin. See [current runbook](phase-3.1.md) and review-policy.md. Earlier sections retain historical POC context.

# AI-Assisted Development Workflow — POC Operating Contract

> Status: **Proposed for owner review**. This document describes how ChatGPT Tech Lead, Codex CLI, GitHub, and `my_wfc` should cooperate. It does **not** enable an automated scheduler, Codex dispatch, OAuth, or a Workflow Console integration.
>
> Scope: `builacviet/techlead-review-poc`. For implementation details, see [AGENTS.md](../AGENTS.md), [architecture.md](architecture.md), [review-policy.md](review-policy.md), [my-wfc.md](my-wfc.md), and [handoff.md](handoff.md). Those documents remain authoritative for their respective implementation-specific contracts.

## 1. Goals and boundaries

The POC tests a repeatable, evidence-based delivery loop:

```text
Owner authorizes scope
       |
ChatGPT Tech Lead: discovery -> PRD/AC -> architecture -> task breakdown
       |
Codex CLI: plan -> test -> code -> verify -> inspect diff -> commit/push -> PR
       |
Coder/authorized client: register_task_handoff -> READY_IN_REVIEW
       |
ChatGPT Tech Lead: claim -> inspect exact Git SHA -> GitHub PR review
       |                                      |
       +-----------------------------> submit_review -> SQLite
                                              |
                             APPROVED / CHANGES_REQUESTED / ESCALATED
```

For this POC, **GitHub is the source of truth for repository code, commits, PRs, and inline discussions**. **`my_wfc` SQLite is the source of truth for workflow state, completed review rounds, recorded findings, and handoffs**. A GitHub review comment does not itself advance the SQLite state machine; a successful `submit_review()` is required. An MCP `APPROVED` verdict is not permission to merge.

`order_service` is the small application under review. `my_wfc` is a separate Python package in the same repository; neither package imports the other. Keep application behavior separate from orchestration infrastructure.

## 2. Roles and authority

| Role | Owns | Must not do |
| --- | --- | --- |
| **Owner (human)** | Project priorities, scope sign-off, explicit merge/deploy authorization, escalation decisions | Assume a recorded verdict alone authorizes a merge |
| **ChatGPT Tech Lead** | Clarify requirements, author/review PRD and acceptance criteria, architecture and coding/security standards, atomic task breakdown, task authorization, code/security review, findings and verdict | Claim tests or model effort that cannot be verified; silently extend scope or auto-merge |
| **Codex CLI (Coder)** | Read authorized context, implement bounded change, write tests, verify, inspect diff, push branch/PR, provide real evidence, fix findings | Self-approve; fabricate test results; change protected policy or approve own work |
| **`my_wfc` service** | Validate task/branch/SHA, issue review claims, persist transactional state, enforce two-round policy, expose task/review context | Perform an AI review, execute Codex, merge code, or silently bypass authentication |
| **GitHub** | Persist source commits, branch heads, PR discussions, line comments, reviews | Act as the authoritative workflow state machine |

Current POC authentication uses a server-side static Bearer credential for seven protected MCP tools. **Role-based authorization is not yet implemented**, so the table above is an operating responsibility contract, not a claim of enforced per-agent permissions.

## 3. Documentation and decision gates

Follow this sequence; for small changes, keep artifacts concise rather than creating ceremony.

1. **Context** — Read `AGENTS.md`, the current task, architecture, review policy, and relevant source/tests.
2. **Discovery / scope** — State the problem, expected outcome, risks, dependencies, and out-of-scope items. Owner confirms scope.
3. **PRD + Acceptance Criteria** — Specify observable behavior, edge cases, failure behavior, and testable acceptance criteria. Do not begin implementation with unresolved blocking requirements.
4. **Tech Spec / Architecture / UI (if needed)** — Define boundaries, invariants, persistence, error handling, data flow, interfaces, and security threats. Record design choices and tradeoffs; update the spec when implementation exposes a gap.
5. **Atomic task breakdown** — Each task has one bounded goal, prerequisites, owned files/interfaces, acceptance criteria, test plan, definition of done, risk, and requested review effort (`instant` / `medium` / `high`).
6. **Implementation** — Codex: Plan -> Test -> Code -> Verify -> Read diff -> Commit/Push -> PR. Work only on the authorized branch/worktree.
7. **Tech Lead review** — Evaluate requirements, diff, tests, architecture, security and earlier findings against the **exact remote head SHA**. Submit findings and one allowed workflow verdict.
8. **Merge / delivery** — Only after explicit owner authorization: revalidate head SHA, review evidence and relevant checks; then merge. Deployment, monitoring and production rollouts are outside this POC unless separately approved.

### Task contract (minimum)

A task document under `docs/tasks/` should have:

- **Task ID / title / motivation / scope** and explicit out-of-scope work
- **Acceptance criteria** identified as AC1, AC2, etc., plus relevant negative cases
- **Architecture constraints / affected modules / dependencies**
- **Required tests, security checks, and Definition of Done**
- **Authorized repository, branch, base SHA**, requested review effort and whether *verified* effort is mandatory
- **Owner authorization** and handoff expectations

Do not confuse a proposed document with implementation authorization. Changing approved ACs or architecture requires an explicit decision and an updated task/spec.

## 4. Coding, test, and security standards

### Coding and quality

- Apply repository-specific [AGENTS.md](../AGENTS.md) rules: Python 3.11+, type hints, clear names, focused functions, explicit errors, and deterministic pytest coverage.
- Maintain boundaries: `order_service` must not depend on network, SQLite, or MCP. `my_wfc` must not alter order-domain semantics.
- Preserve domain invariants described in [architecture.md](architecture.md); use `Decimal` correctly (reject float coercion, non-finite and invalid values).
- Prefer small, reviewable commits; update tests and documentation alongside behavior changes.
- Include actual test commands and observed outputs in every coder handoff. Report unrun tests as **not run**; never turn claims into CI or independently verified evidence.
- Inspect `git diff` before push, check for unexpected files and limit changes to the authorized task.

### Security review gate

- No credentials, Bearer tokens, session headers, GitHub auth materials, private SQLite databases, backups, or logs in commits, task docs, PR comments or ChatGPT tool arguments.
- Keep protected `my_wfc` read/write APIs authenticated; only `ping()` and `get_server_info()` are intentionally public.
- Verify full branch SHA and base ancestry using GitHub; reject stale, mismatched, or unverifiable heads. An earlier approval does not cover later commits.
- For state-changing code, inspect auth boundary, input validation, least privilege, transaction/rollback behavior, concurrency, idempotency and secrets handling. Document residual risks.
- Current transport uses local loopback behind an HTTPS tunnel; do not open an unauthenticated public workflow endpoint to make the POC work.
- Reasoning effort is **requested metadata**, not proven execution. When trusted High-effort attestation is required but absent, automatic approval must fail closed.
- Security findings are mapped to explicit ACs or security requirements, with severity, path/line, impact, suggested remediation and how to verify the fix.

## 5. Handoff and review protocol

### Coder -> review queue

1. Codex implements and tests on a dedicated `task/<id>` or approved infrastructure branch.
2. Codex pushes and opens a PR against the authorized base, recording the exact remote head SHA.
3. The authorized client or operator calls `register_task_handoff(task_id, repository, branch, base_sha, new_head_sha, handoff_file_path, test_summary)`.
4. The service verifies the authorized task and GitHub references before transitioning from `AWAITING_HANDOFF` to `READY_IN_REVIEW`; a corrected new SHA can also return a `CHANGES_REQUESTED` task to the queue.

**Current status:** The MCP API exists; automatic Codex-to-MCP handoff and a Workflow Console controller are **not implemented**.

### Tech Lead -> review result

1. `list_review_queue()` discovers ready work; `get_review_context(task_id)` provides ACs and prior findings.
2. `claim_review(task_id, expected_head_sha)` leases the task and changes it to `IN_REVIEW`; claiming alone does not consume a round.
3. Read the actual base-to-head Git diff, changed files, task docs, tests, PR discussion and prior finding resolutions. Review against the **claimed** SHA, not a moving branch name.
4. Post relevant comments on the GitHub PR (inline where possible). A PR comment/approved status is distinct from the MCP verdict.
5. `submit_review()` persists verdict, findings, prior resolutions, round and SHA in one transaction. `get_review_result()` and `get_coder_assignment()` expose the recorded outcome and next action.

**GitHub account caveat:** A GitHub account cannot officially APPROVE its own PR. In that case, a ChatGPT Tech Lead assessment can be posted as a **COMMENT**, but it must not be represented as GitHub's formal `APPROVED` review state. The separate MCP decision still follows its own security and review policy.

### Two-round rule

```text
AWAITING_HANDOFF
  -> READY_IN_REVIEW
  -> IN_REVIEW
       |-- accepted -> APPROVED (terminal)
       |-- round 1 fails -> CHANGES_REQUESTED
                                 -> new pushed SHA + handoff
                                 -> READY_IN_REVIEW -> IN_REVIEW (round 2)
                                                      |-- accepted -> APPROVED
                                                      |-- fails -> ESCALATED (terminal)
```

An expired review claim can be recovered into `READY_IN_REVIEW` without using a round. Claims are time-limited (current default 900 seconds); a reviewer must not assume a 15-minute scheduler interval prevents expiry. An expired or stale claim cannot submit. No automatic third review; terminal tasks need explicit owner/Tech Lead handling.

Review findings must specify IDs, AC/security mapping, severity, location, actionable remediation and verification. A later review explicitly records each earlier finding as `FIXED`, `NOT_FIXED`, `PARTIALLY_FIXED`, or `NOT_VERIFIED`. Never rewrite earlier findings or history.

## 6. Current evidence vs. future scope

### Delivered source / verified GitHub facts (as of 2026-10-31)

- The Phase 2.2 source migration is merged into `main` as PR #1. `my_wfc` and `order_service` coexist in separate source packages.
- `my_wfc` exposes seven authenticated workflow APIs, SQLite five-table history, exact-SHA checks, claim leases and a two-round verdict policy.
- The historical TASK-001 remains `ESCALATED` (2/2) with F-001 fixed in round 2 and F-002 outstanding. Do not initiate round 3.
- The disposable `POC-REVIEW-001` fixture uses `poc/review-baseline` at the original baseline SHA.
- Codex reported 61 passing tests and smoke/packaging checks during migration; these results were **not independently rerun** by ChatGPT Tech Lead.

### Not yet verified or implemented

- **Integration gate:** ChatGPT -> Cloudflare Tunnel -> `my_wfc` connectivity and protected transport credentials; previous connector calls failed. Local smoke tests are not proof of this path.
- **Scheduling gate:** ChatGPT polling/claim/review/submit end-to-end with an actual authenticated client.
- **Future only:** automatic Codex MCP handoff/dispatch, role-specific credentials or OAuth, PR-to-MCP cross-reference metadata, Notion synchronization, Workflow Console orchestration, CI/deploy automation.

Until the integration gate passes, do not claim the workflow is autonomous. Do not weaken authentication for a demo. Any new feature or policy change requires owner approval and a separate task.

## 7. POC acceptance checklist

- [ ] Local `my_wfc` server and tunnel are reachable from ChatGPT; `ping()` works.
- [ ] The ChatGPT connector discovers all seven workflow tools; authenticated access works without placing tokens in chat/tool arguments.
- [ ] `get_review_context("TASK-001")` reproduces immutable historical findings and `ESCALATED` state without reopening it.
- [ ] `list_review_queue()` includes the disposable baseline fixture, not TASK-001.
- [ ] One exact-SHA `claim_review()` and `submit_review()` flow produces persisted state, correct history and idempotent replay.
- [ ] Restart/reconnect preserves SQLite state; stale-SHA and unauthenticated writes fail closed.
- [ ] GitHub PR review/discussion and MCP verdict are independently inspectable and not mislabeled as identical approval events.
- [ ] Only after those checks: consider turning on scheduled polling, with claim lease duration and runtime measured before enabling concurrent runs.
