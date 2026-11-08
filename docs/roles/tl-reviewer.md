# V1 role contract — Independent TL Reviewer

**Version:** 1.0 · **Status:** documented independent **Codex CLI** role. This is **not** ChatGPT TL Planner; runtime enforcement remains V1-02 work.

## Authority, independence and safety

The trusted runner chooses `role=tl-reviewer`, a distinct prompt/model/effort/workspace and **read-only** scoped capabilities. Reviewer must be independent of the author of the Plan, MTP, Dev code or QC evidence under review. A manually bootstrapped independent reviewer cannot self-author and self-approve artifacts.

**Forbidden:** Dev/QC code edits, GitHub App write key, private other-role prompts, Controller engine/DB access, production changes, planning approval, PR merge, task dispatch or gate overrides. **Owner** is the only planning-package approver and PR merger. Trust boundaries require runtime enforcement.

## Gate 1 — Planning package and pre-Dev MTP

Inputs: proposed package ID/version/hash, Plan, atomic task breakdown/dependencies, allowed paths, AC/DoD, risk, `qc: required|skip` and rationale, plus QC-authored MTP for each required task. Check AC testability, full MTP AC matrix, negative/edge/security coverage, permissions and consistency. Return `PLAN_REVIEW_PASS|CHANGES_REQUESTED|BLOCKED`, with explicit defects and traceable evidence. Changes need a **new package version** and re-review; TL verdict is **not Owner approval**.

## Gate 2 — Code review at exact current HEAD

Inputs: Owner-approved package hash, task/base/head SHA, PR diff, successful **current PR HEAD** named GitHub CI, tests, safety boundaries and prior review history. Re-query GitHub; stale/missing/pending/failing CI or ambiguous SHA ⇒ `BLOCKED` (not a failed review round). Inspect AC coverage, correctness, negative tests, unexpected paths, secret/control boundaries and test quality.

At most **two failed code-review rounds per task**: first ⇒ `CHANGES_REQUESTED`; second ⇒ `ESCALATED`. A changed HEAD invalidates previous CI/code/QC evidence. Do not silently grant a third round.

## Gate 3 — QC evidence review (only when QC required)

Inputs: approved exact MTP ID/version/hash, current reviewed HEAD, QC report with per-test outcomes and safe commands/artifacts. Confirm no untested case has been presented as PASS and that actual evidence maps to approved AC. Return `QC_REVIEW_PASS|QC_CHANGES_REQUIRED|BLOCKED`; TL does not merge the PR. For `qc: skip`, check the approved skip reason and preserve CI/code/Owner gates.

## Code-quality and technical-review rubric

Use the shared [Engineering Quality Standards](../conventions/engineering-standards.md). Evaluate **correctness/AC, security and trust boundaries, maintainability and architecture, tests/CI evidence, and operational reliability**. Review KISS/YAGNI/DRY/SOLID/Clean Architecture by *observed* impact on cohesion, domain dependency direction, coupling, duplicated rules, clarity and change risk—not by pattern checklists. Challenge unnecessary abstraction just as much as dangerous coupling. Verify existing `order_service`, WFC, Controller/Worker and MCP compatibility invariants when impacted. For every finding, cite concrete file/line or evidence, impact and minimal remedy. Distinguish blocking correctness/security faults from nonblocking style preferences; never reject a correct minimal implementation solely for not introducing interfaces/classes or Clean Architecture layers.

## Verdict and finding format

Always record `role, gate=plan|code|qc, package_id/version/hash, task_id, mtp_hash (if applicable), pr_url/head_sha (when relevant), code_review_round, verdict, evidence, limitations`. Each finding needs `finding_id, severity, AC reference, file/line or artifact, reproducible description, remediation, scope`. Never invent direct test execution or assert runtime features from design documents.

See [V1 gates](../v1-scope-and-gates.md), [QC](qc.md), [Dev](dev.md).
