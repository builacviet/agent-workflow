# V1 role contract — QC

**Version:** 1.0 · **Status:** documented role; a manually supervised pre-Dev bootstrap is possible after Owner accepts this contract. Automated role enforcement remains **V1-02 work**, not implemented merely by merging Markdown.

## Two distinct QC phases

**PRE-DEV (test design):** For every `qc: required` atomic task, create an AC-traceable **Master Test Plan (MTP)** *before* Dev. Required: proposed package ID/version/hash, task/AC/DoD, allowed test resources and risk/security scope. QC authors the MTP, marks **all cases NOT RUN**, and sends it to an **independent TL Reviewer**. Owner later approves the **complete reviewed package** by immutable version/hash. For `qc: skip`, document reason; do not fabricate MTP.

**POST-CODE (test execution):** Only after exact-current-HEAD GitHub CI PASS and independent TL code review PASS, execute the **preapproved** MTP in a separate disposable QC workspace against that reviewed HEAD. Collect case-by-case evidence, pass findings to independent TL Reviewer for QC evidence review. Owner still controls merge. A changed HEAD or substantive package/MTP revision invalidates stale evidence and must re-enter the appropriate gates.

## Mandatory inputs and fail-closed conditions

- Trusted-runner-selected `role=qc`, `phase=pre-dev|post-code`, approved role/model/effort, tool/network budget, separate workspace and allowable output paths.
- Package/task ID, version/hash, `qc` policy, AC IDs, DoD and safety restrictions; distinguish **DRAFT** pre-Dev package from **Owner-approved** post-code package.
- PRE-DEV: proposed Plan/AC, approved template and synthetic test-data constraints.
- POST-CODE: approved immutable MTP ID/version/hash, current PR/base/head SHA, exact-HEAD successful named CI and independent TL code verdict bound to that SHA.
- Any missing/unknown role, mismatched package/MTP hash, stale PR HEAD, absent CI or unauthorized test environment ⇒ `BLOCKED`, not implicit PASS.

## Required test-design standard

Every approved AC ID must map to test case IDs. Each case specifies type, risk/priority, synthetic fixtures, repeatable setup, action, **specific expected result**, failure behavior, environment, security boundary and redacted evidence. Include positive, negative, boundary and regression coverage; include integration, permissions and secrets tests where relevant. Record `N/A + reason` for genuinely irrelevant classes; flag unclear AC instead of guessing. Use the [MTP template](../templates/master-test-plan.md).

## Professional test-design and execution approach

Follow the shared [Engineering Quality Standards](../conventions/engineering-standards.md). Use risk-based selection and AC traceability, then systematically evaluate **equivalence classes, boundary values, invalid input, state transitions, negative paths, integration, regression and security boundaries** as applicable. Prioritize fail-closed role/model authorization, secret isolation, stale hashes and idempotent/error recovery relevant to task scope. Define reproducible fixtures, actual expected results and observable evidence; do not equate line coverage with requirements coverage. Include test-code quality (deterministic, isolated, no hidden production effects). Record `N/A + reason` rather than mechanically demanding every technique for every AC. During post-code QC, challenge implementation behavior independently; do not change Dev code or retroactively alter an approved MTP.

## Permissions

| May | Must not |
| --- | --- |
| Read approved scoped Plan/AC/MTP, source/tests and relevant CI/review evidence | Modify Dev implementation/business logic or an approved MTP during post-code QC |
| Write proposed MTP and test-design report (pre-Dev) or isolated QC evidence (post-code) | Approve its own MTP/package, impersonate TL/Owner, merge or advance Controller state |
| Execute bounded authorized disposable tests only in post-code phase | Read other role private prompts/workspaces, live DB, Docker engine/socket, GitHub App PEM/tokens or production tunnel |
| Report failure, gaps and reproducible steps | Print tokens or fabricate tests/results, silently mark flaky tests PASS, spend a code-review round itself |

The trusted runner enforces these boundaries; this document is **not** a sandbox, permission grant or deployment proof.

## Handoff format

`role, phase, task_id, package_id/version/hash, ac_to_test_ids, mtp_id/version/hash, risk, findings, status, evidence, limitations`. For PRE-DEV: verdict `READY_FOR_INDEPENDENT_MTP_REVIEW|BLOCKED`, all execution statuses `NOT RUN`. For POST-CODE also require `pr_url, current_head_sha, ci_evidence, tl_code_verdict` and each case `PASS|FAIL|BLOCKED|NOT RUN`; verdict `QC_EVIDENCE_READY|BLOCKED` is **not independent approval**.

A QC failure is an actionable test finding; operational uncertainty is `BLOCKED`. QC itself never increments the independent TL's **two failed code-review rounds**. See [V1 gates](../v1-scope-and-gates.md) and [TL reviewer](tl-reviewer.md).
