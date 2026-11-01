# DRAFT — AI Software Development Framework

> **Status: DRAFT / DISCUSSION ONLY — NOT APPROVED**
>
> Captured: 2026-11-01 (Asia/Ho_Chi_Minh). Intended for future detailed discussion with the owner.
>
> This document is a working proposal, **not** an implementation authorization or an override of `AGENTS.md`, `docs/poc-workflow.md`, `docs/review-policy.md`, `docs/architecture.md`, or `docs/my-wfc.md`. No automatic MCP/state-machine changes, CI modifications, agent installs, or merges are authorized by it.

## 1. Goal and source-of-truth boundaries

Build a reusable AI-assisted software engineering framework from discovery through design, implementation, independent QC, Tech Lead review and controlled delivery. Avoid requiring the human owner to manually relay context between agents. Keep requirements, architecture decisions, task scope, changes, validation evidence, defects and review results traceable.

- **GitHub:** source/docs, commits, Pull Requests, line comments, discussions, CI evidence.
- **my_wfc + SQLite:** task lifecycle, handoff, exact SHA review state, findings and review rounds.
- **ChatGPT Tech Lead:** engineering decisions and review verdicts, constrained by verified evidence.
- **Codex:** implement and self-test authorized work.
- **Independent QC:** test from business requirements independently of Coder, with actual build/environment evidence.
- **Owner:** final authority for priorities, key design choices, scope, merge and release.

A GitHub COMMENT/APPROVE event and an MCP workflow `APPROVED` verdict are **different events**. MCP approval is **not** merge authorization. GitHub does not allow an account to officially approve its own PR.

## 2. Roles and accountability (proposed)

| Actor | Owns | Must not assume |
| --- | --- | --- |
| Human Owner | Product direction, approvals for material scope/architecture changes, escalations, merge/release | That any agent or MCP verdict automatically authorizes delivery |
| ChatGPT Tech Lead / Solution Architect | Discovery, PRD and AC, solution/backend/frontend/data/API architecture, UI/UX and design governance, coding/security standards, ADRs, technical-debt control, atomic task breakdown, code/security review | It has executed tests, achieved unverifiable effort, or has permission to change accepted scope |
| Codex Coder | Plan, code, tests, verification, diff inspection, commit/push, PR and handoff, correction | Self-approval, silent spec/architecture changes, merge rights |
| Independent QC Agent (future role) | Testability review, independent test-plan/test-case design, integration/E2E/regression/exploratory/UI/accessibility verification, defect reports and retest | That Codex's own test report proves independent verification |
| my_wfc | Verified handoff, review queue/claims, immutable review/finding history, exact-SHA checks, two-round policy | AI code reviews, Codex dispatch or QC execution |
| GitHub | Code and documentation versions, PR review discussions and external checks | Authoritative SQLite workflow state |

Current `my_wfc` has no dedicated QC state, QC actor enforcement, scheduler or autonomous handoff. The separate QC role is **conceptual** until explicitly designed and implemented. Current static Bearer access does not enforce these distinct roles.

## 3. Proposed end-to-end lifecycle

```text
[0] Context: AGENTS.md, conventions, architecture, task policy
       |
[1] Discovery: goals, MVP, assumptions, risks and non-goals
       |
[2] PRD + user stories + numbered acceptance criteria --> sign-off
       |
[3] Tech Spec + architecture (system, backend, frontend, API/data)
    + UI/UX + design system + security + NFR + ADRs --> sign-off
       |
[4] Independent QC reviews testability and drafts test plan from AC
       |
[5] Atomic task breakdown, dependencies, DoR, DoD and review effort
       |
[6] Codex: Plan -> Test -> Code -> Verify -> Diff -> Commit/Push -> PR
       |
[7] Independent QC tests exact build/SHA; defects -> Codex fix/retest
       |
[8] Tech Lead reviews exact remote SHA + QC evidence + code/security
    -> GitHub PR comments -> MCP submit_review
       |-- Round 1 failed: CHANGES_REQUESTED -> new SHA
       |-- Round 2 failed: ESCALATED (terminal; no automatic round 3)
       +-- Passed: APPROVED (workflow verdict, not merge)
       |
[9] Explicit owner merge/release authorization; CI/CD + observability
```

The ordering of QC and Tech Lead review is **not final**. Explore making QC a pre-review gate so ordinary QC fixes do not unnecessarily consume one of the two completed Tech Lead review rounds. Do not change the existing MCP state machine before a separately approved specification.

### Minimum task contract

Every bounded task should declare ID, purpose, authorized scope and non-goals, numbered ACs and edge cases, architecture/interfaces and affected modules, risk/security considerations, required tests, definition of done, dependencies, repository, branch, base SHA, review effort requirements, owner authorization and expected evidence.

If implementation uncovers missing requirements or an architectural gap, propose a spec change and obtain an explicit decision before implementing outside the approved scope.

## 4. Engineering disciplines to standardize

- **Product:** discovery, MVP, PRD, stories, AC, traceability and change control.
- **Architecture:** component boundaries, dependency direction, architecture diagrams, tradeoffs, ADRs, technical debt, performance/reliability constraints.
- **Backend:** module/layer boundaries, business rules, transactions, concurrency, idempotency, validation, errors, auth/authz.
- **Frontend:** routing, state management, data fetching, component structure, performance, error/loading/empty/success states.
- **UI/UX:** user journeys, wireframes, responsive states, accessibility, design tokens, reusable components, design review and visual QA; Figma is optional.
- **API & data:** request/response contracts, persistence, schema/indexes/constraints, migration, backups, retention/privacy.
- **Coding:** per-stack conventions, names, code organization, dependency management, lint/static checks, code review expectations.
- **Security:** threat model where justified, secrets, least privilege, input validation, auth boundaries, dependency scanning and release review.
- **Testing/quality:** unit/integration/contract/E2E/regression/exploratory/visual/accessibility tests appropriate to risk and supported by actual evidence.
- **Delivery/operations:** CI/CD, release checks, rollback, logs/metrics/traces, alerting, incident runbooks.

Prefer practical templates over generic slogans (e.g. a backend spec should state transaction boundaries and failure behavior, not merely name an architecture pattern).

## 5. Independent QC proposal

### Before coding
- Receive PRD/AC and relevant UX/API contracts from Tech Lead, ideally without relying on Codex's implementation plan.
- Write a risk-based test plan and AC-to-test matrix, including negative cases and requirements ambiguities.
- Escalate untestable acceptance criteria before coding.

### After implementation
- Identify **tested commit/build SHA**, test environment, credentials/data fixtures where applicable, and actual command/tool outputs.
- Independently execute applicable unit/regression checks, API/integration tests, UI/E2E tests, exploratory cases, accessibility/visual checks.
- Report `PASS`, `FAIL`, `BLOCKED` or `NOT_TESTED` per relevant area; record defect IDs, severity, reproduction steps, observed vs expected behavior and evidence.
- Let Codex correct defects, then retest the new exact SHA. Tech Lead evaluates QC evidence but does not equate it with code/security review.

**Open QC policy questions:** mandatory vs risk-based gate, independent environments and identity, where QC sits relative to Tech Lead claim, defect ownership and retest rules, whether QC rejection consumes a review round, whether MCP needs `IN_QC` and `QC_FAILED` or merely QC artifacts linked to existing tasks.

**POC-first recommendation:** pilot with a QC report linked on a GitHub PR; do **not** add new MCP statuses, APIs or authentication bypasses yet.

## 6. Proposed Markdown framework layout (illustrative; do not generate all yet)

```text
AGENTS.md
README.md
docs/
  README.md                    # document index/status/authority/reading order
  poc-workflow.md              # current POC operating contract
  review-policy.md             # existing review policy
  architecture.md              # existing factual architecture
  product/
    requirements.md
  architecture/
    system.md
    backend.md
    frontend.md
    data-api.md
    adr/
  design/
    ui-ux.md
    design-system.md
  standards/
    coding.md
    security.md
    testing.md
  quality/
    qa-strategy.md
    independent-qc-policy.md
    defect-management.md
  delivery/
    definition-of-ready.md
    definition-of-done.md
    runbook.md
  templates/
    prd.template.md
    tech-spec.template.md
    task.template.md
    handoff.template.md
    review.template.md
    adr.template.md
    test-plan.template.md
    qc-report.template.md
    bug-report.template.md
  tasks/
  drafts/
    ai-development-framework.md # THIS file; unapproved
.agents/skills/                 # future Codex repo-local skills
.github/PULL_REQUEST_TEMPLATE.md
```

Avoid duplicating rules. Existing documents should be inventoried before any reorganization. Separate normative policy and reusable templates from historical evidence and reports. `AGENTS.md` should be the concise agent entry point and link to authoritative docs.

### Potential documentation cleanup after approval

- Existing `AGENTS.md` and `TASK-001.md` contain historical/proposed wording; clarify present task status without rewriting history.
- `docs/review-policy.md` still reads as a proposal even though the `my_wfc` state-machine subset is implemented; separate delivered behavior from unverified automated orchestration.
- `docs/handoff.md` mixes instructions and history: separate reusable handoff template from factual migration/verification records.
- `README.md` should link the workflow entry point, and a `docs/README.md` should define document hierarchy and reading order.

## 7. Skills strategy (proposed, NOT installed)

**Codex Coder:** optional `UI/UX Pro Max` for actual frontend work, frontend design-to-code when Figma is used, Playwright/web E2E testing, custom backend conventions, secure coding, test-first implementation and PR feedback handling.

**ChatGPT Tech Lead:** PRD/discovery, system architecture, UI/UX review, atomic task planning, security/code review, ADR and technical-debt governance. Figma integrations may help design artifacts.

**Independent QC Agent:** requirements-to-test design, API/integration testing, Playwright E2E, regression/exploratory testing, visual/accessibility QA and defect reporting.

**First custom-skill candidates:** `techlead-system-design`, `techlead-task-planning`, `coder-implementation-gate`, `independent-qc-gate`. These are *names proposed by the team*, not claims of available published packages. **Markdown policy is the source of truth; skills reference and apply it**. Review third-party skill scripts, requested permissions, install location and version before installation.

## 8. POC implementation constraints

- Preserve the current seven protected `my_wfc` APIs, SQLite review invariants, exact-SHA checks and two-round maximum unless separately approved.
- No automatic Codex dispatch, scheduler, QC state, Notion sync, OAuth or Workflow Console integration implied by this draft.
- Existing TASK-001 history stays `ESCALATED` at two completed Tech Lead reviews, with no third automatic round or merge.
- Previously reported tests are not automatically independently verified. ChatGPT↔MCP authenticated live end-to-end integration remains a separate validation gate.
- A ChatGPT agent operating through the same GitHub account as PR author cannot submit an official self-APPROVE review; record evidence accurately.

## 9. Next discussion agenda / unresolved decisions

1. Is the team model exactly **Owner → Tech Lead → Coder + Independent QC**, or should there also be a separate Security Reviewer/Release role?
2. Should QC be mandatory on every task, only on medium/high-risk changes, or determined by affected layer?
3. What is the definition of independence for QC (separate agent/session, isolated tests/environment, no production-code edit rights)?
4. When should independent QC run; who owns defect triage; do QC retests count toward Tech Lead review rounds?
5. Which standards are global templates versus technology-specific/project-local documents?
6. Which sign-off gates require owner approval, and how are scope/architecture exceptions documented?
7. Which 4–6 templates are essential in the first Foundation PR, and how should the existing docs be cleaned up without rewriting history?
8. Should GitHub PR comments be linked into `my_wfc` by metadata, and what evidence is needed before automating?
9. When to install/select UI/UX Pro Max, Playwright and custom engineering/QC skills; what security vetting is required?
10. How to transition this framework to Workflow Console or Notion *after* the POC gates are validated?

## 10. Proposed next milestone (requires later owner approval)

- **Foundation:** document index and authority hierarchy, concise agent rules, PRD/Tech Spec/Task/Handoff templates, Definition of Ready/Done, review policy clarification.
- **Engineering:** backend/frontend/data/API/design standards, security and testing, ADR template and technical debt.
- **Quality:** independent QC policy, test plan/test case/QC report/bug templates and a manual PR-based QC trial.
- **Delivery & automation:** skills, PR templates, CI/observability, MCP connectivity and potential scheduler/Workflow Console/Notion integration.

No workstream is authorized merely by appearing in this draft.

---
**Resume prompt for the next conversation:** "Read `docs/drafts/ai-development-framework.md` on branch `docs/draft-ai-engineering-framework` in `builacviet/techlead-review-poc`. We want to finalize our AI Software Development Framework, including independent QC, document hierarchy, architecture/UI/UX/backend/frontend standards, templates, skills and gates. Do not implement or merge yet; first identify open decisions." 
