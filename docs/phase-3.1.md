# Phase 3.1 operator runbook

Implementation authorization and ACs: [PHASE-3.1](tasks/PHASE-3.1.md). Independent Tech Lead review remains pending. No automatic merge, verdict, third review, OAuth, dashboard, Notion or Workflow Console v2 integration.

## Boundaries

The Controller is a trusted **host-side** Python process. Docker CLI access remains with the operator; no container gets the Docker socket. It polls SQLite every 60 seconds after each synchronous attempt and permits at most one dispatch across Controller processes. `BEGIN IMMEDIATE`, a unique global slot and a durable `(task, completed-round, input-SHA)` key provide exclusion/idempotency. An OS process lock also excludes reconciliation while the Controller is alive. Use one physical SQLite database on one host, not independent copies or a network filesystem.

`coder_dispatches` is an additive table; existing version-1 workflow tables and two-round semantics are unchanged. The Controller selects only task IDs in its local configuration and tasks already provisioned through the existing local authorization entry point. Eligible states are AWAITING_HANDOFF and CHANGES_REQUESTED, with fewer than two completed reviews. Other states never dispatch. No task is authorized by a prompt or by adding it only to Controller JSON.

A dispatch lease is deliberately non-expiring for scheduling purposes. Its deadline bounds container execution and the trusted host publication path through completion. A shared context budget caps Git/OpenSSL subprocess waits, GitHub HTTP timeouts and SQLite lock waits to the remaining time. Checks before/after GitHub calls and before SQLite commit prevent expired work from starting later publication steps or being recorded DONE. A monotonic limit also prevents a backward wall-clock adjustment from extending execution. Cleanup is explicitly outside the execution budget so termination remains possible after expiry. An already-started remote request may have succeeded when a timeout is observed: no rollback of GitHub is claimed, and the slot remains blocked for reconciliation. If the Controller crashes, the slot survives and a second coder cannot start; the container also has its own deadline. Crashes before launch, after GitHub publication or before SQLite handoff are ambiguous and require operator reconciliation. This is at-most-once automatic execution, **not** a claim of atomic exactly-once GitHub/SQLite delivery.

Only tracked regular source files are copied to a disposable snapshot. Git metadata, host credentials, SQLite, ignored files, PEM and secret directories are not mounted. Committed `.env.example` and `.runtime/*.example` templates are context only; they cannot be modified by the coder. The configured write path list is enforced again before publication. Symlinks, junctions, submodules, hardlinks, special files, oversized files/snapshots and out-of-scope writes fail closed. Existing executable modes are preserved; new files are ordinary files. Generated pytest/Python cache directories are excluded.

Codex CLI is pinned at 0.161.0. Model choice is required in local config; no model or effort execution is claimed by that setting. `codex exec` uses workspace-write, ephemeral state, no user config or execpolicy rules, and an isolated tmpfs home. Root filesystem is read-only, user is 10001:10001, capabilities are dropped, no-new-privileges is enabled, with CPU/memory/process/output/time limits. Source snapshots are writable to that UID under an operator-private scratch parent. Keep that parent private using NTFS ACLs on Windows or mode 0700 on Linux. Source/output may be sensitive; retain and delete local scratch only under operator policy.

The code container receives **its own dedicated OpenAI API key**; it is not an isolation boundary from that model credential. Use a separate, limited-budget key for trusted private tasks. Do not reuse v2 auth volumes, user Codex homes or shared production credentials. The separate test container receives no credential and has network disabled. Tests run as `python -m pytest -q`; host never executes task code. Raw output stays in private scratch; generated handoffs record the observed command/exit status. Test success is evidence for Tech Lead review, not approval.

## GitHub App

Register/install `builacviet-wfc-coder` on **only** `builacviet/techlead-review-poc`. Required repository permissions: Contents write, Pull requests write, Metadata read. No Actions/workflow, administration or merge automation is requested. Configure the positive numeric App ID and Installation ID locally. The App slug, ID, installation repository/ID and suspension status are checked before token minting. Resolve bot numeric ID via `/users/builacviet-wfc-coder[bot]`; commit author and committer use `ID+builacviet-wfc-coder[bot]@users.noreply.github.com`.

Store its private key as `.secret/coder-app.pem`, never in Git, prompts, images, Docker environment or logs. Local OpenSSL signs RS256 JWTs (iat 60 seconds back, exp 540 seconds forward). The installation token request is limited to this repository and the two write permissions; expiry is validated, refresh occurs with 120 seconds remaining, and shutdown attempts revocation. Requests use fixed HTTPS GitHub API origin, reject redirects and sanitize errors. No token is returned to MCP clients or Codex. MCP uses App verification when any App configuration is present; partial/broken App configuration never silently falls back to gh. Legacy gh verification remains available when all App settings are absent.

Before dispatch/publication, verify allowlisted canonical linked worktree, clean tracked/untracked tree, exact branch/origin/input SHA and GitHub base ancestry/head. After an earlier App publication, the clean local worktree can fast-forward to the exact authorized remote SHA using ephemeral App-authenticated Git fetch; hooks/helpers are disabled and credentials are not stored. Divergence/stale heads fail closed.

Publication uses GitHub Git Data API to create blobs/tree/commit with the bot identity and advance the branch with `force=false`. This has normal fast-forward push semantics without giving the coder any GitHub token. Existing bot-authored draft PRs are looked up and updated; unexpected authors or non-draft PRs block. No merge request is issued. Remote head is rechecked before `WorkflowService.register_task_handoff`, preserving exact-SHA verification and review policy. There is still no cross-system lock against a branch moving immediately after a GitHub check; Tech Lead must review and approve the exact current SHA.

## Independent sandbox profile

`docker/coder-seccomp.json` is derived directly from Moby profiles `seccomp/default.json` at commit `4b8214fc4971e06172938a1f1554ccff6647ee80`, plus seven allow rules required for nested unprivileged bubblewrap namespace setup: clone, unshare, mount, umount2, pivot_root, setns, sethostname. See `docker/MOBY-PROFILES-LICENSE`. These operations run with all outer capabilities dropped; this is not a claim of proven minimal syscall permissions. Default Docker seccomp failed the actual namespace probe; the independent profile passed. No unconfined/privileged mode is used. The Controller verifies the operator-configured SHA-256 before using the profile. Host kernel/LSM requirements can vary; rerun the probe on the target host and fail closed rather than weaken isolation.

Profile SHA-256: `ae759f8cbb0a2dfe62d517be5e73c405666b309ec55988b1ef60f92b59d9a1f8`.

## Prepare and verify without credentials

From the Phase 3.1 worktree, use Python 3.11+ and Docker Desktop Linux containers:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,mcp]"
python -m pytest -q
docker build -f docker/coder.Dockerfile -t my-wfc-codex:phase31 .
python tests/live_phase31_smoke.py --image my-wfc-codex:phase31
```

The opt-in smoke creates its own ignored snapshot/database and exact-name containers only. It checks workspace writes/outside denial, runs the complete suite in the credential-free container and checks timeout/confirmed cleanup. It does not call a model, GitHub App or existing MCP service. Use an image digest/immutable local image ID for activation if local tags may change.

## Operator steps for live activation

1. Create `.secret`, `.runtime`, `data/controller` with private host permissions. Supply the dedicated PEM and separate OpenAI key `.secret/openai-api-key.txt` locally; do not paste them into chat. The older POC worktree had a PEM during discovery, but it was not used or copied; App/installation IDs were not configured in the inspected environment/config. Verify the intended App/installation before reuse.
2. Copy `.runtime/controller.json.example` to `.runtime/controller.json`. Replace example App IDs, model, all absolute paths, image and task ID/worktree/write paths/handoff path. Retain the checked profile digest. The database path must refer to `data/controller/workflow.sqlite3` when using the standalone MCP compose file. The v2 guard checks actual directory roots (including resolved aliases), not the ordinary `workflow.sqlite3` filename. JSON is operator-controlled, never generated from model output.
3. Create a **new dedicated** linked worktree and remote task branch for a synthetic task from an approved baseline. Do not add TASK-001 or reuse v2 directories. Keep its branch clean. Author a task spec/ACs and provision that task through `WorkflowService.provision_task` locally in the new database using a strict TaskSpec. Use `require_verified_effort=true` when required; missing trusted effort evidence still blocks approval. Context paths must include the intended handoff. No production or historical DB is copied automatically.
4. Optionally run MCP against the same new physical DB using `compose.controller.yaml` (separate `my-wfc-phase31` project, loopback port 18001). Copy `.runtime/controller-mcp.env.example` to `.runtime/controller-mcp.env`, configure the same numeric App/installation IDs, a new minimum-32-character Bearer secret and reviewer identity. On Linux make `data/controller` writable to UID 10001. Start only this project with `docker compose -f compose.controller.yaml up -d --build mcp`. It enforces `WFC_POC_NO_AUTH=0`; no tunnel or v2 service is started. Do not point the host Controller at the old named-volume DB: separate copies would be inconsistent.
5. For an existing MCP deployment only, `compose.app.yaml` is an optional App-verifier overlay on `compose.yaml`, with operator-set App ID/installation environment and the ignored PEM bind. It does not migrate state or activate Controller. Prefer the standalone setup for the first synthetic test.
6. Run `python -m my_wfc.controller .runtime/controller.json --once`. Check the draft PR actor is `builacviet-wfc-coder[bot]`, the commit author/committer numeric email, full remote SHA and persisted READY_IN_REVIEW handoff. Inspect local private test output and handoff at that SHA. Then allow continuous polling by omitting `--once`; stop with Ctrl+C. `--once` returns a nonzero exit on failure, leaving the dispatch slot blocked.
7. Have ChatGPT Tech Lead claim/review/submit round 1 on that exact SHA using the authenticated MCP client. A real finding should yield CHANGES_REQUESTED. Run one correction; expect a new bot commit/updated draft/handoff for round 2. A second failure escalates, and terminal tasks never dispatch again. Do not invent findings for a real task or submit a coder verdict. No more than two completed reviews. Verify restart persistence, stale remote rejection and token renewal in the live environment before calling this end-to-end path proven.

App token/PR creation and a real model-driven correction cycle were **not run** during implementation because dedicated App IDs and model credentials were not provisioned. API behavior is covered by injected deterministic tests, not represented as live bot evidence. The Phase 3.1 implementation PR itself is created through the operator's configured GitHub account, not misattributed to the bot.

## Crash / timeout / publication reconciliation

1. Stop the Controller process first. Never delete rows to force another attempt. OS locking rejects reconciliation while another process is running.
2. Read `coder_dispatches` locally for the dispatch ID/task/input SHA/status/deadline; do not expose workflow secrets/history in public logs. ACTIVE survives crash; BLOCKED records an operational failure. The global slot remains occupied even after deadline.
3. Inspect remote branch, bot PR, committed handoff and SQLite status. If publication succeeded, complete the existing handoff through the authorized workflow API after exact-SHA verification, rather than recoding. If no publication occurred, preserve the failed attempt and explicitly reauthorize a new task identity after fixing the cause. The old idempotency key is never automatically retried. Operational failure does not consume a review round.
4. After reconciliation, run `python -m my_wfc.controller .runtime/controller.json --acknowledge DISPATCH_ID --reconciled`. This terminates/removes only the exact `my-wfc-DISPATCH_ID-{code,test,probe}` containers, verifies they are absent, and acknowledges the slot. Failure to reach Docker keeps the slot. The operator flag attests that external Git/PR/handoff effects have already been reconciled; it does not automate that judgment.
5. Restart polling only after verification. Local scratch snapshots and raw test logs are not automatically deleted. Do not operate, restart, inspect data/auth volumes or change the worker of Workflow Console v2.

## Sources

- [Official Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode): workspace-write, stdin, ephemeral runs and API-key automation.
- [GitHub App JWT](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app) and [installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app).
- [Pinned Moby seccomp source](https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json).
