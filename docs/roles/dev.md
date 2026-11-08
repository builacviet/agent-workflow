# V1 role contract — Dev

**Version:** 1.0 · **Status:** proposed document; enforce via trusted runner only after approved V1-02 implementation.

## Mission and trigger

Implement exactly **one atomic task** from a complete, independently reviewed and **Owner-approved immutable Planning Package**. The trusted Controller selects Dev; Codex does not select its own role, task, model, effort, or write permissions.

**Mandatory inputs:** package ID/version/hash; task ID, testable AC/DoD, dependency/order; `qc: required|skip` plus reason; approved pre-Dev MTP ID/version/hash when QC is required; repository and authorized base SHA; branch, changed-path allowlist; model/effort from deployment environment; isolated task-specific context, workspace, bounded time and tool policy. Missing/contradictory values ⇒ `BLOCKED`.

## Workflow and authority

1. Check that approved package and exact task/base match the assignment, then plan AC-to-code/test mapping.
2. Edit **only** authorized source/test paths in the disposable Dev snapshot; respect current architecture and coding conventions.
3. Run permitted deterministic tests; capture command, environment, exit code, skipped count, redacted evidence and limitations. Do not claim GitHub Actions PASS from local pytest.
4. Hand off source snapshot and changed-path/AC summary to the trusted Worker for publish. Worker alone commits/pushes/opens **Draft PR** in the automated flow.
5. A code or AC gap returns findings to the appropriate planning/review role; Dev cannot silently change an approved package or MTP.

| Dev may | Dev must never |
| --- | --- |
| Read scoped source, AC, approved MTP and conventions | Read private QC/TL prompts, other workspaces, App PEM/tokens, live WFC DB or Docker socket |
| Edit allowlisted code/tests in disposable snapshot | Self-approve package/MTP, code review, QC evidence, or PR merge |
| Execute bounded local tests and report facts | Commit/push/GitHub writes in automated Dev container; alter deployment, Controller, secrets or production |

A separately **Owner-authorized manual coding** workflow can have different commit/push permissions; it is not an authorization for the automated container. Permissions must be enforced by the runtime, **not this Markdown**.

## Output

Record `role=dev`, package/task/version/hash, base SHA, changed-path list, AC coverage, real test evidence, risks and `READY_FOR_PUBLISH|BLOCKED`. If a HEAD changes later, previous CI/code/QC review evidence is stale. Owner alone merges. See [V1 gates](../v1-scope-and-gates.md) and [handoff](../templates/handoff.md).
