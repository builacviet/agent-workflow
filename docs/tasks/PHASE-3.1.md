# PHASE-3.1 — isolated GitHub App coder controller

Owner authorization: current user request on 2026-11-04 authorizes this exception to the prior no-dispatch boundary. TASK-001 remains unauthorized and ESCALATED. Repository: builacviet/techlead-review-poc. Branch: codex/phase-3.1. Base: c5e0574ab793e5322f943a57206daf4d4efbea04. Requested review effort: high; actual model/effort unverified. Independent Tech Lead review pending, maximum two rounds; no merge.

## Acceptance criteria

- AC1: configured positive App/installation IDs, ignored .secret PEM, RS256 JWT, repository-scoped short-lived installation token, renewal and sanitized failures. Verify expected App slug and installation repository. Resolve the bot numeric ID from GitHub for commit email; App token creates draft PRs. Never claim operator-created work was bot-created.
- AC2: independent pinned Codex CLI image; non-root, read-only root, dropped capabilities, no Docker socket, no v2 images/data/auth volumes. Runtime receives a tracked-file snapshot, task context and its own OpenAI credential, never GitHub credentials, SQLite or PEM. Tests execute in a separate credential-free network-disabled container.
- AC3: poll authorized SQLite every 60 seconds, concurrency one across processes using BEGIN IMMEDIATE and a unique active slot. Dispatch only explicitly configured tasks/worktrees with AWAITING_HANDOFF or CHANGES_REQUESTED and fewer than two completed rounds. Durable idempotency key covers task, completed round and input SHA.
- AC4: persist container name before launch. Timeout/error/crash keeps the global slot blocked until an operator confirms containers have stopped and reconciles Git/PR/handoff. Never automatically retry an uncertain execution or expire a lease into a second coder. No review round consumed by operational failures.
- AC5: exact repo, branch, canonical worktree, clean tree, local input SHA, remote branch head and base ancestry checks before dispatch and publication. Reject symlinks, secret paths and writes outside operator-configured paths. Ordinary push only; no force/merge. Recheck remote new head before workflow handoff.
- AC6: successful execution runs python -m pytest -q, records actual evidence, bot commit/push, idempotent draft PR lookup and authenticated-service-equivalent local handoff. Review state/policies remain owned by WorkflowService, no coder verdicts. Preserve existing MCP authentication.
- AC7: deterministic negative/concurrency/crash/idempotency tests, complete-suite evidence, operator runbook and draft PR with exact SHA; disclose live checks not performed. No v2 operation, OAuth, dashboard, Notion or third review.

## Design / test plan

Trusted host-side Controller owns SQLite, Docker CLI, Git metadata and GitHub App. Codex receives only a disposable tracked-source snapshot. Controller never runs task code on host; tests run separately without credentials. A durable dispatch row acts as a non-expiring safety lease with a runtime deadline; heartbeat expiry is diagnostic, never permission to launch again. Crashes require explicit operator reconciliation (safety over automatic liveness). Existing schema version and review tables remain compatible; controller adds one independent table. Configuration is local, operator-controlled JSON, never supplied by the model. It explicitly selects task IDs, canonical dedicated worktrees, write prefixes and credential paths. Existing task authorization is still mandatory. Runtime model is operator-configured, not silently selected.

Unit tests inject clocks, GitHub transport/signing and runtime callbacks. Real Docker build/version/synthetic filesystem smoke are attempted when available; real App and model calls require separately provisioned secrets. Definition of done: tests, focused source/docs, commit/push, draft PR and exact-SHA handoff; approval is not part of coder completion.


## Review follow-up F1/F2

Input SHA: 1123267f7988e6f34202fc0a65fe2bb1548ba216. The review at https://github.com/builacviet/techlead-review-poc/pull/5#pullrequestreview-5772173953 is a COMMENT with recommended CHANGES_REQUESTED, not a persisted formal Tech Lead verdict. Owner authorized correcting F1 (AC3/AC7) and F2 (AC4). Fix scope: allow the documented independent shared-DB filename while retaining canonical v2-directory rejection; extend the existing deadline through trusted Git/GitHub/SQLite publication and completion, retaining blocked reconciliation and post-expiry cleanup. Add deterministic config/clock/transport regressions and rerun complete suite/Docker. Acceptance criteria and the maximum-two-formal-round policy are unchanged; Codex records no verdict or approval. Awaiting independent review of the new exact SHA.
