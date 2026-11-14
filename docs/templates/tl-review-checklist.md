# Manual Development — ChatGPT Tech Lead PR review checklist v1.0

**Applies to:** ChatGPT Tech Lead reviewing manual Coder Draft PRs for this repository. Product runtime role specifications do not add review steps here.

**Authority:** Owner/TL-approved task and [AGENTS.md](../../AGENTS.md); the relevant [engineering standards](../conventions/engineering-standards.md) sections and observed CI/PR evidence. Review proportionately; do not invent gates.

## Review the exact snapshot

- [ ] Record task ID, review round, base SHA, current PR HEAD SHA, changed-path allowlist and reviewer/evidence limitations.
- [ ] Review **complete approved-base and PR-base diffs**, task AC, shared contracts and the Coder's **checked self-review in the PR description**. Verify Draft state and GitHub CI on the **exact reviewed HEAD**.
- [ ] **Handoff evidence:** Check the [PR handoff table](handoff.md) for each applicable self-review status and real command, exit/count, tested SHA and gaps. Cross-check with changed files, tests and CI. A missing per-task Markdown file is **not** a blocker; missing/unverified **required evidence** still blocks approval.
- [ ] For changed Python files, verify Ruff check/format actually covered the relevant **PR diff**; a push event may contain only a docs-only latest commit. Verify required CI job name/event, checked SHA and test scope rather than accepting a green badge alone.
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

**Manual repository development flow:** TL task → Codex code/tests/self-review → Draft PR → GitHub CI → ChatGPT TL review → Owner merge → TL verifies. Do not require QC/MTP, Planning Package hashes or a separate Codex Reviewer to develop the product.
