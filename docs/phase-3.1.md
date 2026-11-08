# Phase 3.1: worker owns GitHub, WFC owns workflow state

CURRENT source/runbook architecture: approved 2026-11-05 and merged as [Phase 3.1 PR #5](https://github.com/builacviet/agent-workflow/pull/5), squash `e09a054e96d56cec1e24590e959bff3ec1a990e3`. Requested effort high; actual model/effort unverified. This replaces older Controller-push/MCP-remote-verifier instructions. **Merge is not evidence a deployment is live or production-approved**; operator activation and actual environment remain separate checks.

## Responsibilities

Worker dev (`coder_worker`) owns GitHub App and all Git/GitHub calls. Tech Lead uses its GitHub plugin for exact-SHA diff/comments/inline review and checks head/base ancestry immediately before verdict. `agent_workflow` stores authorized tasks, handoff, claims, findings and verdicts, validates local SHA/state/round rules and never contacts GitHub. Controller polls SQLite every 60 seconds, one atomic dispatch slot, starts/stops Docker worker phases and records worker-reported results; it does not push or verify remote state.

DB access uses WorkflowRepository and WorkflowUnit interfaces. WorkflowService contains no SQL or sqlite3 calls. SQLiteWorkflowRepository handles schema initialization, reads and atomic writes. Dispatch storage is a separate SQLite adapter. Service accepts a repository implementation via constructor; tests inject a memory repository. Existing version-1 schema/history stay compatible. No evidence-schema expansion was made. Authenticated clients remain trusted reporters; per-role authorization is still an operating contract, not implemented RBAC.

## Runtime phases

1. prepare: trusted worker receives context on stdin and App PEM read-only. It checks authorized repo/branch/input SHA via GitHub, clones into its own dispatch directory and exports a source snapshot.
2. code: Codex receives only the snapshot and a persisted ChatGPT device-login volume. No GitHub key/token, Git metadata, result or DB mount.
3. test: same snapshot, `python -m pytest -q`, no credential and network disabled. Controller compares source hashes before/after tests to reject mutations. **CURRENT prepares/code/publish do not explicitly set `--network=none`; actual egress restrictions depend on the runtime environment and are not verified here.**
4. publish: trusted worker alone receives App PEM. It validates changed paths against input Git tree, writes a handoff, commits with resolved bot name/numeric email, performs normal `git push` (never force), verifies remote SHA, creates/updates only a bot-owned draft PR and writes result.json outside coder/test mounts.
5. Controller validates result task/repo/branch/base/input/full head/PR URL locally and registers reported handoff in SQLite. WFC does not confirm remote truth. Tech Lead claims and reviews using GitHub plugin, then submits the exact-SHA verdict to authenticated MCP.

Each dispatch uses a fresh directory under the operator-allowlisted linked worktree's `.runtime/dispatch/ID`; host Git metadata/source are not mounted. Controller checks canonical paths/v2 exclusion but leaves Git checks to worker. The persistent global slot/idempotency key prevents duplicate dispatch even after timeout/crash. A failed phase or uncertain remote effect stays BLOCKED until operator reconciliation. No automatic retry or third review. Source snapshots/result/test files should be retained with private host ACLs for diagnosis; Linux dispatch files must be writable by UID 10001.

The worker App uses repo-scoped short-lived tokens, refresh/revoke and fixed GitHub API origin. One bounded HTTP process per request enforces total DNS/TLS/headers/body timeout; credentials travel only through stdin, not argv/files. On timeout the child is killed/reaped, with five seconds cleanup grace. OS process creation may be uninterruptible; already-accepted remote effects cannot be rolled back. These are worker concerns, not WFC features.

## Prepare

From the dedicated Phase 3.1 worktree:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,mcp]"
python -m pytest -q
docker build -f docker/coder.Dockerfile -t my-wfc-codex:phase31 .
python tests/live_phase31_smoke.py
```

The smoke uses disposable source/SQLite, local loopback tests and exact-name containers; no App/model calls or v2 resources. Worker CLI stays pinned to 0.161.0 / Node base digest. Non-root, read-only root, cap-drop, namespace seccomp hash and workspace sandbox remain required. `docker/coder-seccomp.json` derives from pinned Moby default plus seven namespace syscalls; license/provenance remain in repo. Rerun probe on each target host; do not use privileged/unconfined fallback.

## Live operator setup (not performed by Coder)

- Configure `.runtime/controller.json` from its example: explicit task IDs, canonical dedicated linked worktrees, write/handoff paths, same physical DB, image, model, timeout, App/installation IDs and ignored credential paths. Use the configured GitHub App PEM and `codex_auth_volume=techlead-review-poc_codex_auth`; no OpenAI API key is used. Controller mounts them only in their relevant phases; it does not read private contents or mint tokens. Never reuse v2 auth/data/images/volumes.
- Provision a new synthetic task locally via WorkflowService.provision_task, using its authorized branch/base/context paths. Create that remote branch through worker/operator GitHub access. Do not reopen TASK-001. Existing task authorization and two-round caps remain mandatory.
- Optional MCP: copy `.runtime/controller-mcp.env.example`, configure Bearer secret/reviewer identity, create data/controller with UID 10001 permissions and run `docker compose -f compose.controller.yaml up -d --build mcp`. This independent MCP binds loopback 18001 and shares data/controller/workflow.sqlite3 with host Controller. MCP has no App/GH_TOKEN mount or Git/GitHub binary requirement. Existing compose service remains separate; do not create divergent DB copies.
- Run `python -m agent_workflow.controller .runtime/controller.json --once`, inspect bot commit/draft PR/result/handoff. For continuous polling omit --once. Stop before reconciliation.
- Tech Lead independently checks GitHub before claim/verdict. `approval_current` is always unknown rather than an online freshness check. Branch movement outside the workflow requires operator/Tech Lead action; WFC cannot detect it autonomously.
- **After timeout, crash or lost Docker CLI/daemon connection:** consult the [CURRENT recovery matrix, evidence limitations and post-reconciliation dispositions](guides/dispatch-recovery.md) **before** any operator acknowledgement. Caught exceptions can mark a dispatch `BLOCKED`; abrupt process death may leave `ACTIVE`. **Both retain the global slot.** Confirm the correct workflow DB/dispatch row and Docker endpoint, compare the derived container name, dispatch workspace mount and engine-reported creation metadata, and independently check exact remote branch/PR head, body, owner and WFC handoff. **CURRENT does not persist container IDs or prove ownership from names/labels; inability to establish a safe identity or exclude late create means STOP and escalate, not acknowledge.** The `--reconciled` CLI flag is solely an operator assertion; cleanup by name runs **before** the dispatch row is checked and does not validate remote effects. Only if an Owner-authorized operator has independently verified and recorded all preconditions may they consider `python -m agent_workflow.controller .runtime/controller.json --acknowledge DISPATCH_ID --reconciled`. This **only releases the slot**; the original task/round/input SHA key remains spent. After a successful handoff, WFC may already have progressed from `READY_IN_REVIEW` to `IN_REVIEW` or later verdict states **while the dispatch slot is still held**; check the exact-SHA `HANDOFF` event and review history, not just the current WFC task status. A failure of final container cleanup **after** the handoff can also prevent dispatch `DONE` without invalidating the handoff; inspect engine evidence separately. If publish/handoff is missing or inconsistent, there is **no authorized automatic or manual replay/repair path** in this runbook: keep the block, preserve evidence, request a separately approved remedy. Never edit the DB, fabricate input SHA, manually register handoff, or advance review rounds to force recovery.

The persisted ChatGPT session is accessible to its code phase; do not share its volume with another project. Separate test phase has none. Live App mint/push/PR, model cycle and authenticated Desktop review integration remain unverified until operator-run synthetic acceptance. Coder test evidence is not independent Tech Lead approval.


## Device-login volume

Owner selected ChatGPT device login instead of API keys. Volume `techlead-review-poc_codex_auth` mounts RW at `/home/coder/.codex` only for code phase, allowing CLI credential refresh. It was initialized separately from v2; auth.json is UID 10001/mode 0600 and never copied/read into the checkout/logs. Existing logged-in volume must exist; Controller inspects rather than silently creating it. Code entrypoint requires auth.json, clears CODEX_API_KEY/OPENAI_API_KEY and explicitly selects file credential storage. MCP/test/prepare/publish never receive this volume. Missing login fails with an operator instruction, not API-key fallback. A fresh container `codex login status` can verify session reuse without a model call. This auth integration does not activate Controller dispatch or change MCP credentials.
