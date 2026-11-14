# Agent Workflow V1 — Scope and Gate Contract

> **Status:** **Owner-approved V1 delivery scope** via [merged PR #22](https://github.com/builacviet/agent-workflow/pull/22) (merge `86c47481e0af6fc66c30d9e1cd70882e06ced678`). This contract does not by itself grant deployment, credential access, or task-execution approval.
>
> **Baseline:** `main` at `4a23d5dfac28c1f8ee71187fe00cac06e880d4d1` (following PR #21). [V1-00 acceptance criteria and planning draft](https://app.notion.com/p/412f92014abd81528012fd5ea320cde3).
>
> **Visual overview:** [V1 architecture + planning/execution flowcharts](v1-visual-flows.md) (Mermaid). Read these first for orientation; the written gates below remain authoritative.

## 1. Scope and precedence

V1 delivers the smallest sequential, approval-gated workflow using the existing Phase 3.1 architecture, not a new orchestration platform.

- **In scope:** ChatGPT TL planning; independent Codex CLI Dev, QC, and TL Reviewer roles; role-specific prompt/permissions and model/reasoning-effort from deployment environment; trusted Controller/Worker; existing Docker CLI, SQLite/WFC and GitHub App; GitHub Actions CI; Telegram notifications/identity-gated human approvals.
- **Deployment intent:** Keep the host Controller and Docker-CLI architecture; run the Controller under Linux systemd via the separately authorized V1 operations work. Current host engine authority is broad and risky; neither systemd operation nor production hardening is proven by this document.
- **Compatibility:** Preserve Streamable HTTP `/mcp` and the **nine existing MCP tools**, including names, signatures, response contracts and authentication behavior. New functionality may only be added compatibly through separately approved tasks.
- **Out of scope:** new Executor service, Podman/Controller-to-Executor IPC, GHCR or Grafana rollout, epic branches, concurrent/parallel task execution, auto-merge, new orchestration framework, recurring ChatGPT scheduler, and guaranteed ChatGPT-native mobile approval/notification.
- **Architecture override:** For **V1 delivery only**, this contract takes precedence over CL-12/M01 **target implementation** recommendations where they conflict with the above V1 scope. CL-12/M01 ADRs and their historical evidence remain intact and are not rewritten, revoked as history, or evidence of deployed runtime. Current source/tests and verified deployment evidence still govern **as-built** claims; security and least-privilege constraints remain applicable.

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

### Automated Dev/Code stage contract (product feature)

This is **a specification for the product's automated Code stage**, not instructions for the manual Coder developing this repository. The manual team follows [`AGENTS.md`](../AGENTS.md).

- **Activation and inputs:** The trusted Controller selects an approved atomic task from the authorized Planning Package, with package/version/hash, task ID/AC, approved base SHA, allowed changed paths, QC designation and (where required) MTP identity, role/model/tool limits and a bounded disposable snapshot. Missing or inconsistent inputs ⇒ `BLOCKED`. The agent cannot select its own permissions, role, task or approval.
- **Allowed:** Read the assigned accessible snapshot, inspect relevant interfaces, edit only approved code/test paths, and write a concise AC→changed-symbol→required-test mapping. It may author tests as source files when explicitly allowlisted.
- **Execution boundary:** The untrusted Code stage **must not run Python, tests or arbitrary host commands**; only the separate trusted-orchestrated **isolated Test stage** executes test code and produces test/exit/artifact evidence. No live DB, GitHub credentials, private role prompts, Docker socket or unrestricted host/network access is delegated to the Coder. Requirements for sandbox isolation must be enforced by implementation and verified tests, not by this Markdown.
- **Publish boundary:** The Code snapshot has no Git metadata or GitHub credentials. The trusted Worker computes/validates changed paths against the approved allowlist, captures evidence and alone commits/pushes/opens Draft PR. The Code stage must not claim to have run `git diff`, pushed, executed tests or observed CI unless authoritative evidence was supplied.
- **Output and failures:** Return task identity, implemented AC coverage, changed-path proposal, requested tests, risks and `READY_FOR_PUBLISH|BLOCKED`. Tests and CI are `NOT VERIFIED` until trusted Test/CI evidence exists. Ambiguous AC, unapproved cross-boundary edits, changed task identity or unavailable evidence ⇒ `BLOCKED`/clarification, not silent expansion.
- **Independent decisions:** Dev never approves its own package, QC report, review, deployment or merge. Owner retains approval/merge authority, and newer HEAD invalidates old SHA-bound evidence.

## 4. Failure, security and audit invariants

- **`BLOCKED`:** operational uncertainty (network/timeouts, Docker issues, missing checks, uncertain publish, inconsistent GitHub/SQLite state). Do not count it as a code-review failure or automatically retry risky effects.
- **`ESCALATED`:** second failed code-review round, or unresolved planning/review inconsistency requiring explicit human decision. Never exceed the two-round code-review cap.
- Bind planning approvals to immutable package version/hash and code/CI/QC verdicts to exact commit SHA. Stale SHA, missing evidence or unverified model/effort where required must fail closed.
- Keep GitHub App keys, tokens, MCP auth secrets, active SQLite/WFC DB and arbitrary host/engine control out of untrusted agents. Reusing the current **trusted host Controller → Docker CLI** is a known, narrowly scoped V1 architectural choice, not proof of least-privilege engine isolation or authorization to broaden that access.
- Record outcomes and provenance (plan version, task, base/head/merge SHA, reviewer, review round, AC, CI/QC evidence, Owner decision). Do not misstate target design as observed implementation.

## 5. Change control, evidence ownership and rework decisions

**Approved files stay frozen:** Plan, AC and MTP bytes are individually identified in a versioned package manifest with SHA-256 digests. The manifest/package hash is computed and stored **outside files it covers**, to avoid self-reference; trusted Controller/Worker should attest it once V1-02 delivers that capability. For manual bootstrap, Owner/operator records hashes externally. Any byte change after approval is a *new artifact version/hash*; if it affects an approved package, re-review the package and obtain **new explicit Owner approval** before further Dev. An existing approval cannot silently carry over.

**Who revises what:** Dev, QC or TL Reviewer finding an ambiguous, contradictory or untestable requirement must send `clarification_request {artifact_id, AC_or_test_id, question, suggested_options, evidence}` and mark `BLOCKED`. TL Planner authors revised Plan/AC; QC authors revised MTP; independent TL Reviewer rechecks the whole affected planning package; Owner approves the new version/hash. Reviewer does not edit its own review target. After dispatch, a new package version invalidates affected in-flight evidence; Controller later implements safe reconciliation. No agent independently decides that an approved file can be edited “non-substantively.”

**Evidence and changed paths:** For this product's controlled execution, trusted runner/Worker—not an LLM—captures the tested snapshot manifest/digest, sanitized test artifacts, actual exit code and changed-path allowlist result. A claimed `PASS` without verifiable artifacts is `NOT VERIFIED` and cannot clear a gate. Snapshot-only agents receive a trusted read-only diff/manifest when available; they must **not** claim they ran `git diff` without Git metadata. Current Phase 3.1 already has some trusted Worker checks but **V1-02's role-specific attestation and V1-03's exact-CI checks are not yet implemented**.

**Owner-approved V1 product policy (2026-11-09; [decision recorded in PR #27](https://github.com/builacviet/agent-workflow/pull/27#issuecomment-6727494479)): maximum two per product rework loop.** This is a scope/gate contract for the automated product, **not** a retry budget or additional gate for manual Coder tasks in this repository. The linked comment records an Owner instruction conveyed by the Tech Lead, not a separate GitHub-authenticated approval event. The original loops (Plan/MTP review, CI repair, formal code review, post-QC repair) have separate counters; QC evidence review is also capped at two submissions. Owner may revise the policy after observing operations. These are **docs-only targets** except legacy WFC's code-review counter, which already counts every formal submission **including PASS**.

| Counter | Stable key | Increment and cap | Never counts |
| --- | --- | --- | --- |
| **Plan/MTP review** | `package_id` **lineage**, including post-approval change-control revisions | **2 formal independent TL planning reviews** (initial + one revision) over that lineage | Draft edits; insufficient evidence or operational `BLOCKED` |
| **CI repair** | `task_id` across **all** branches/HEADs/triggers, including code fixes and QC-repair HEADs | **2 authorized Dev repair HEADs** after a recorded completed required exact-HEAD `conclusion=failure` | Initial CI failure alone, same-SHA reruns, `cancelled`, `timed_out`, stale, pending, missing or ambiguous checks (`BLOCKED`) |
| **Formal code review** | `task_id` = legacy WFC `completed_review_rounds` | **2 total formal submissions, PASS included**; first failure `CHANGES_REQUESTED`, second failure `ESCALATED` | Operational `BLOCKED` with no formal submission |
| **QC repair/retest** | `task_id` across post-code cycles | **2 Dev repair HEADs** after verified required-case QC FAIL; each requires fresh CI, independent TL **post-QC code revalidation**, and frozen-MTP QC retest | QC `NOT RUN`, uncertain or flaky test results, rerun without authorized source repair |
| **QC evidence review** | `task_id + qc_execution_attempt_id`, unchanged across report revisions | **2 formal TL QC evidence submissions** per authorized execution attempt, initial and one corrected-evidence submission | Draft edits, `BLOCKED`, changing report hash/version alone |

**QC execution attempt IDs (R-1):** Only the trusted Controller may issue a **new** `qc_execution_attempt_id` for (i) the first post-code QC execution on an independently reviewed HEAD, (ii) a separately authorized QC-repair HEAD, or (iii) an **explicit Owner decision**. QC and TL Reviewer cannot request or invent a new ID to reset the evidence-review counter. Retesting an unchanged HEAD, editing a report, changing the harness or resubmitting evidence within the same execution attempt **keeps the same ID**. This is a **product Controller contract to implement**, not existing WFC functionality.

**Post-QC code revalidation versus legacy WFC code review:** Legacy WFC rejects a handoff/review after `APPROVED`; it cannot safely accommodate another formal code-review submission or reset `completed_review_rounds`. **Proposed V1 product behavior:** each authorized QC repair HEAD receives **one independent TL post-QC code revalidation** checking changed diff, AC/security/scope, trusted fresh exact-HEAD CI and frozen MTP, recorded in a **separate future QC-repair gate**, under the **QC repair counter**, not the legacy code-review counter. It applies full independent technical scrutiny but is **not a third WFC `Submission`**. **Until a separate Owner-approved gate and durable record exist, a verified QC FAIL requiring code changes ⇒ `BLOCKED` and Owner reconciliation.** Do not reinterpret the old code verdict as covering the new HEAD.

**CI provenance, failure classification and rerun defense:** Trusted CI evidence must bind `{task_id, pr_url, current_pr_head_sha, tested_sha, check_name, event, conclusion, workflow_run_id, run_attempt, check_run_id, completed_at}` and retain earlier runs at the same SHA. Only a trusted completed required named-check `conclusion=failure` is a definite CI defect; `cancelled`, `timed_out`, pending, stale, missing or inconsistent evidence is `BLOCKED`. A later **success on the same SHA after a recorded failure does not silently clear the gate**: stop `BLOCKED` until Owner explicitly acknowledges the failure, rerun and disposition. Same-SHA reruns do **not** count as Dev CI repairs. Failed CI on code-review-fix or QC-repair HEADs consumes the **same task-lifetime CI repair budget** when an authorized subsequent Dev repair HEAD is attempted.

**Reset and escalation:** On exhaustion, terminal policy disposition is **`ESCALATED`**. If the deployed legacy WFC cannot represent this status for a non-code loop, persist **`BLOCKED` with reason `LIMIT_EXHAUSTED`**, counter key, actual attempt IDs/history and Owner notification; do **not** submit an invalid code verdict. Counter keys and attempts are persisted by the **trusted Controller/WFC** in the future; agent prose never advances or resets them. New HEAD/PR/session/MTP revision, approval of a revised package or a new report digest **never implicitly resets** the existing task/package-lineage counter. Only **explicit Owner action** with recorded reason, old/new identity and prior attempt history can authorize a reset in a newly approved package/policy. At any 2-round limit, **stop and notify Owner** with evidence; no automatic third attempt. An unresolved `BLOCKED` must also notify Owner after the approved incident/timeout path, not sit silently forever. New planning/QC `ESCALATED` state must not be misrepresented as supported by current legacy WFC.

**No runtime authorization:** This maximum is not permission to deploy, retry uncertain effects, bypass an exact-HEAD gate or merge. Owner alone controls package authorization, retry-policy changes and PR merge.

**QC ownership:** Dev owns unit/integration/regression tests included in its approved source PR; QC owns the independently approved pre-Dev *test design* and post-code *execution evidence*. QC may build an isolated test harness from frozen MTP semantics without editing Dev source; its script version/hash and commands are captured in the [execution-report template](templates/mtp-execution-report.md). An additional test must be reported explicitly and cannot turn a frozen MTP case PASS by itself; material MTP change returns to planning approval.

**Quality policy scope:** `qc: skip` must have task-specific, risk-based reason reviewed in the package and approved by Owner; it **does not waive CI, independent code review or Owner merge**. For behavior/security/stateful work prefer `qc: required`. Do not impose a blanket “docs-only” rule without a separate Owner policy decision.

### Product-role review boundaries

Dev implements under approved scope. QC designs the approved test plan then executes it separately. Independent TL Reviewer reviews the plan, code and QC evidence but does not edit artifacts under its own review. **Owner alone approves package versions and merges PRs.** These written product contracts do not enforce permissions; actual role selection, model/effort policy, mounted paths and capabilities must be enforced by the trusted runtime.
