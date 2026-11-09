# V1 role contract — Dev

**Version:** 1.0 · **Status:** proposed document; enforce via trusted runner only after approved V1-02 implementation.

## Mission and trigger

Implement exactly **one atomic task** from a complete, independently reviewed and **Owner-approved immutable Planning Package**. The trusted Controller selects Dev; Codex does not select its own role, task, model, effort, or write permissions.

**Mandatory inputs:** package ID/version/hash; task ID, testable AC/DoD, dependency/order; `qc: required|skip` plus reason; approved pre-Dev MTP ID/version/hash when QC is required; repository and authorized base SHA; branch, changed-path allowlist; model/effort from deployment environment; isolated task-specific context, workspace, bounded time and tool policy. Missing/contradictory values ⇒ `BLOCKED`.

## Workflow and authority

1. Check that approved package and exact task/base match the assignment, then plan AC-to-code/test mapping.
2. Edit **only** authorized source/test paths in the disposable Dev snapshot; respect current architecture and coding conventions.
3. Run permitted deterministic tests; report the commands requested and cross-check **trusted test-stage/runner captured** exit codes, environment, skipped count, artifact digests and redacted evidence. Agent self-report is advisory and cannot clear a gate; do not claim GitHub Actions PASS from local pytest. Dev owns approved source-PR unit, negative and regression tests, **not** the independent immutable QC MTP.
4. Hand off source snapshot and changed-path/AC summary to the trusted Worker for publish. Worker alone commits/pushes/opens **Draft PR** in the automated flow.
5. If AC or approved MTP is ambiguous/conflicting, return a structured clarification request via [change control](../conventions/engineering-standards.md) and mark `BLOCKED`; Dev cannot revise approved Plan/AC/MTP or decide a change is “non-substantive.”

| Dev may | Dev must never |
| --- | --- |
| Read scoped source, AC, approved MTP and conventions | Read private QC/TL prompts, other workspaces, App PEM/tokens, live WFC DB or Docker socket |
| Edit allowlisted code/tests in disposable snapshot | Self-approve package/MTP, code review, QC evidence, or PR merge |
| Execute bounded local tests and report facts | Commit/push/GitHub writes in automated Dev container; alter deployment, Controller, secrets or production |

A separately **Owner-authorized manual coding** workflow may use Git tooling **only** on the named task branch `codex/<task>`, with ordinary push and Draft PR handoff explicitly granted for that task. Manual operation never authorizes push to `main`, force-push, Owner approval, PR merge, out-of-scope writes or credential sharing. Record the immutable package hash, actual changed-path allowlist and exact base/head in handoff; manual permission does not transfer to automated Dev containers. Permissions must be enforced by the runtime, **not this Markdown**.

## Coding and design quality

Follow the shared [Engineering Quality Standards](../conventions/engineering-standards.md) and applicable language/framework conventions. **KISS, YAGNI, DRY, SOLID and Clean Architecture are proportional design heuristics, not mandatory class/layer counts.** Before editing, identify existing domain/storage/Controller/Worker boundaries, public API invariants and AC-to-test coverage. Favor focused understandable functions, explicit types/errors and state validation before mutation. Avoid premature abstraction or out-of-scope refactor. After coding, self-review a **trusted runner-supplied baseline manifest/unified diff** (or allowed comparison of snapshot files) for readability, dependency direction, regression, security/secret handling, testability and contract breaks. The automated Code stage has no Git metadata: do **not** claim a `git diff` ran there. Trusted Worker computes actual changed paths and rejects out-of-allowlist writes. Explain conscious tradeoffs in the handoff.

## Output

Record `role=dev`, package/task/version/hash, base SHA, runner-verified changed paths, **AC implementation coverage (not a gate PASS)**, observed test evidence/reference, risks and `READY_FOR_PUBLISH|BLOCKED`. If trusted artifacts are absent, report `NOT VERIFIED`, not PASS. If a HEAD changes later, previous CI/code/QC review evidence is stale. Owner alone merges. See [V1 gates](../v1-scope-and-gates.md) and [handoff](../templates/handoff.md).
