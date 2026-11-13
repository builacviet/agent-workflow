# Manual Development — ChatGPT Tech Lead PR review checklist v1.0

**Applies to:** ChatGPT Tech Lead review of Codex Draft PRs during **CURRENT DEVELOPMENT** of Agent Workflow V1. This is **not** the automated V1 TARGET QC/MTP/independent Codex TL Reviewer workflow.

**Authority:** Active Owner-approved task/AC and [AGENTS.md](../../AGENTS.md) first; [Engineering Quality Standards](../conventions/engineering-standards.md) defines the quality rubric and [Review Policy](../review-policy.md) governs SHA/rounds. Apply review depth proportionally; avoid extra gates or patterns without concrete benefit.

## Review the exact snapshot

- [ ] Record task ID, review round, base SHA, current PR HEAD SHA, changed-path allowlist and reviewer/evidence limitations.
- [ ] Read the complete diff against the base, applicable task AC, relevant architecture/contracts and Dev self-review. Verify PR state and GitHub CI on the **exact reviewed HEAD**.
- [ ] For changed Python files, verify Ruff lint and format CI evidence on the reviewed HEAD; distinguish changed-file enforcement from a whole-tree baseline cleanup.
- [ ] For PostgreSQL changes, check the [mandatory Session/query/transaction ownership rules](../conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership). Flag duplicated per-adapter transaction ownership, nested commits, changed isolation/timeout/rollback, leaked ORM entities, unsafe SQL or misuse of pinned session vs transaction locks. Verify justified exceptions and live PG CI where required.
- [ ] Check the following groups, recording **PASS / FAIL / N/A / NOT VERIFIED** with brief supporting evidence. An N/A or NOT VERIFIED needs a reason.

| Area | Questions to verify |
| --- | --- |
| **1. Scope & requirements** | Every AC covered? Only authorized paths? No accidental public API, behavior, migration or runtime changes? |
| **2. Clean Code & maintainability** | KISS, YAGNI and meaningful DRY? Small cohesive functions, clear names/types/errors, readable flow, no dead code/magic values or unjustified abstraction? Did Dev proactively fix within-scope self-review defects? |
| **3. Architecture & design** | Module ownership and dependency direction preserved? Separation of concerns, justified interfaces and minimal coupling/blast radius? |
| **4. Security & trust boundaries** | Validation, fail-closed failures, secrets/logs, permissions, path safety, Docker/network/subprocess/SQL boundaries and authorization invariants? |
| **5. Correctness & reliability** | Happy/error/edge cases, compatibility, transactions/rollback, idempotency, cleanup, timeouts, concurrency/restart when applicable? |
| **6. Tests & CI evidence** | Meaningful positive/negative/boundary/regression cases; deterministic tests; architecture gate and required CI tied to current SHA; skips/gaps disclosed? |
| **7. Operations & documentation** | Safe config/defaults, diagnosable nonsecret errors, runbooks, recovery/rollback and platform compatibility when applicable? |

## Findings and verdict

- [ ] Every actionable finding names **severity, file/line, concrete evidence, affected AC/risk and smallest viable remediation**. Distinguish demonstrated defects from suggestions.
- [ ] Classify: **BLOCKER** (security/authorization breach, data loss, critical AC failure), **MAJOR** (material regression/high-risk omission), **MINOR** (limited maintainability/testability impact), **SUGGESTION** (optional improvement). Do not block only for a preferred style/pattern.
- [ ] Confirm outstanding BLOCKER/MAJOR findings and incomplete mandatory evidence cannot be called APPROVED. Minor/advisory issues are recorded proportionally; avoid quota-consuming cosmetic rework.
- [ ] Submit a **SHA-bound verdict** (`APPROVED`, `CHANGES REQUESTED` or `BLOCKED` for missing evidence); follow existing review-round/escalation policy. Any subsequent HEAD change invalidates previous approval.
- [ ] Record GitHub review/comments and link the result in Notion. **Only Owner** authorizes merge; TL re-verifies merge before the next dependent task.

**Manual DEVELOPMENT flow:** TL task → Codex code/tests/self-review → Draft PR → GitHub CI → ChatGPT TL review → Owner merge → TL verifies. Do not require QC/MTP, Planning Package hashes or a separate Codex Reviewer to develop the product.
