# CL-12.5 — Trusted Executor security ADR and platform decision gate

Status: **Owner-merged security architecture documentation** ([PR #11](https://github.com/builacviet/techlead-review-poc/pull/11), squash `e4ce254d24cd46d952ff5d814304c58fde3941b3`), selecting **Rootless Podman CLI + dedicated unprivileged Linux-host Trusted Executor (B3) for development**. CL-12.6 [PR #12](https://github.com/builacviet/techlead-review-poc/pull/12) and consolidated CL-12.7 [PR #13](https://github.com/builacviet/techlead-review-poc/pull/13) were also Owner-merged as **documentation**. **Parent CL-12 final sign-off, Executor implementation, native Linux security validation and production readiness remain separate/unverified gates.** Original decision date: 2026-11-06; merge status verified on 2026-11-07. Task: https://app.notion.com/p/410f92014abd816996ace95343795c15

Base: 937d0ba2203bb4cff0a63ea5a6ac3bc094b5137e. Scope: **this document only**. No code, Docker, credentials, live DB, Controller or host operations. CL-12 final design sign-off and implementation authorization are separate. This replaces neither the CL-02 permissions work nor the CL-11 schema plan.

## 1. Decision and evidence boundary

**Historical B2 Docker candidate (superseded for this development phase by Owner-selected B3 in §9):** Controller container -> authenticated, narrowly typed Trusted Executor interface -> executor container -> **dedicated rootless worker Docker daemon**, isolated from the host-wide daemon. The daemon must be provisioned without privileged Docker-in-Docker (DinD), must not expose a generic container-create API to Controller/MCP/Codex, and must pass all platform/security gates below. The executor is trusted and still requires strong internal authentication, allowlists, ownership and audit. Rootless reduces daemon privileges; it does not prove sandbox isolation.

**Historical B1 alternative (not automatically permitted):** a minimal trusted host executor with authenticated local-only IPC and strictly limited Docker actions **only after Owner explicitly chooses it**, following a failed or impractical B2 feasibility evaluation. No automatic fallback to rootful daemon access or privileged DinD. A rootful generic Docker socket proxy is not an adequate boundary.

Docker documentation says rootless Docker daemon/containers can run under an unprivileged user, but the common docker:dind-rootless image is launched with --privileged to disable security restrictions. The latter is **not an acceptable B2 implementation** here:
- https://docs.docker.com/engine/security/rootless/
- https://docs.docker.com/engine/security/rootless/tips/

Current observed repository (not deployment proof):
- src/my_wfc/controller.py: one durable dispatch slot, assignment/SHA/round revalidation, BLOCKED on uncertain failure; Controller uses the local Docker adapter.
- src/my_wfc/coder_runtime.py: controller currently calls Docker CLI for named worker stages; --read-only, UID 10001, cap-drop ALL, no-new-privileges, resource caps, stage-specific mounts, selected seccomp, task allowlists; code stage needs Codex egress/auth, test/probe no network; trusted prepare/publish get a scoped GitHub App key mount.
- src/coder_worker/main.py and docker/coder_entry.py: trusted snapshot/GitHub operations separated from model-run code/test by entrypoint mode and mounts, but worker image currently contains broadly copied src.
- src/my_wfc/coder_runtime.py:324-325 stage is created with 0700 and then chmod(0o777); src/coder_worker/main.py:83-84 broadens source directory permissions. Mode/ACL exposure needs a CL-02 and CL-12.5 threat/host verification. This observation is **not a proven exploit**.
- Current source/config and unit tests cannot prove effective Linux or Docker Desktop UID, seccomp, daemon privilege, mounts, network boundaries or exploitability.

## 2. Actors, assets, trust boundaries

| Actor/zone | Trusted capabilities required | Denied capabilities |
| --- | --- | --- |
| ChatGPT client / MCP ingress | OAuth-scoped review requests; public ping/info | Any container engine/socket, host paths, GitHub App PEM, Codex auth material, direct DB/admin |
| MCP service | Auth verification + workflow repository on approved DB | Worker images, daemon/socket, arbitrary job execution, app PEM |
| Controller | Durable dispatch ownership, checks on task/round/SHA, bounded typed job submission | Podman/Docker command creation, arbitrary host mounts, GitHub App credentials, direct engine API/socket |
| Executor (trusted) | Strictly authenticated request parser, local job policy, provisioning/stop/reap and host-real workspace mapping | Dynamic policy from prompt, arbitrary image/mount/flags, unrestricted TCP API, database schema mutation |
| Rootless worker engine context | Only approved ephemeral worker lifecycle under the dedicated unprivileged Executor identity, via local Podman CLI | Any host-wide Docker/Podman socket, privileged containers, host-wide mounts/devices |
| Trusted prepare/publish | Scoped short-lived App key/token, network access to GitHub, Git metadata and validated snapshot/publish | Workflow DB, generic Docker controls or persistent unscoped credentials |
| Untrusted Codex code stage | Task snapshot/write allowlist, model authentication and bounded model network egress if validated | .git, App PEM/token, workflow DB, any engine socket, host paths, executor secrets |
| Untrusted pytest/test stage | Test snapshot and ephemeral nonsecret scratch | Network, Codex auth, App PEM, .git, DB, daemon |
| order_service | Independent stdlib example | Any workflow, executor or worker dependency |

Protected assets: GitHub App PEM and installation token; model auth volume; private source and Git object provenance; task source/output allowlist; workflow SQLite and dispatch ledger; container engine socket; host workspaces and other projects (including Workflow Console v2); worker result.json; access logs, backups and configuration.

Explicit boundary transitions: authenticated MCP -> domain workflow (not job execution); trusted Controller -> typed executor (not Docker API); trusted prepare -> sanitized code snapshot; code -> credential-free no-network test; verified test/digest -> trusted publish. No LLM-generated text, task metadata, symlink, worker output or container name is inherently trusted.

## 3. Minimal executor API contract (proposal, not implementation)

The caller is a **specific authenticated Controller identity**, not a ChatGPT task/tool or Codex process. Private Unix socket or mutually authenticated local transport between the Controller container and host Executor; reject unauthenticated/duplicate/expired credentials and restrict network reachability. No endpoint that accepts raw Podman/Docker CLI args or generic engine operations. Socket ownership, identity verification, namespace and address mapping remain implementation test gates.

Requests are schema-versioned and strictly validated:
- submit(job_id, dispatch_id, task_id, input_sha, approved_image_digest, workspace_id, stage, deadline, resource_profile_id, policy_version, request_nonce)
- get_status(job_id)
- stop(job_id)
- reconcile(job_id), operator-only acknowledgment after inspecting external effects.

Job ID and dispatch ID must bind to the durable DB assignment and exact full SHA; executor separately checks an authenticated authorization claim and persisted idempotency record, not just syntactic validity. stage is an enum: prepare, code, test, publish (probe only under explicitly authorized security validation). Client cannot select mounts, hostname, device, network_mode, seccomp path, UID or arbitrary flags; executor resolves a versioned policy from **operator-owned config**, enforces approved image by immutable digest, fixed stage transitions and bounded CPU/memory/pids/timeouts/output.

Return a bounded typed result: job_id, request digest, state, monotonic lifecycle timestamps, phase artifact digest, reason code and externally verifiable immutable publish reference where applicable. Reject oversized/unknown fields, paths, unapproved image tags, path traversal, extra engine options, invalid SHA, stale claim or deadline and conflicting duplicate job IDs. Log correlation IDs without secret values. Remote Git truth is verified by the worker and independently by Tech Lead; executor does not invent remote freshness.

State machine: NEW -> PREPARING -> CODING -> TESTING -> PUBLISHING -> DONE; failures/timeout/host restart/unknown remote effect -> BLOCKED until operator reconciliation. Repeated identical submit must return existing status, never launch a second job. Changed payload with same id must fail. Publish must not run unless code/test succeeded, source digest unchanged and the deadline/authorization still hold. Retain existing two-round review policy independently; executor cannot approve a PR or increment review rounds.

## 4. Capability and filesystem policy

- Validate workspace_id against Controller-provisioned registry, then resolve engine-host-real directories under one approved workspace root. Forbid arbitrary caller host paths, bind-source overrides, relative escapes, symlink/junction/reparse traversal, hard-link surprises and mounts into other task or v2 project roots. Recheck target identity before each stage/mount.
- Use least-privilege ownership and an explicit cross-UID mapping; no world-writable stage by default. Investigate stage.chmod(0o777) and current worker source chmod before any permission refactor. On Windows Docker Desktop and Linux separately verify effective ACL/mode, not just displayed bits.
- Code/test get a **sealed source snapshot without .git** or secrets; prepare/publish separately mount only the privileged material they need, for bounded lifespan. Model auth belongs to code only; test gets neither model nor App credentials. Never log keys, tokens or worker stdout containing secrets.
- Use read-only container root, no-new-privileges, all caps dropped, nonroot UID, read-only mounts except explicitly required scratch, independently validated seccomp/AppArmor, pids/CPU/memory/tmpfs and a hard time budget. An operator-maintained profile checksum and image digest are required; fail closed when unavailable. No --privileged, host PID/network, extra devices or discretionary security-opt flags.
- Network: test/probe **none**; code may require model endpoint egress and must be constrained to verified destination/port/DNS policy, not assumed offline; trusted prepare/publish need approved GitHub endpoints only. Reject any request for unrestricted host networking or arbitrary endpoint.
- Worker process, signal and orphan cleanup must be checked after timeout, cancellation, Controller or executor crash. An unverified cleanup leaves dispatch BLOCKED; do not retry a possibly published task.

## 5. Historical comparison: B2 versus B1 (superseded by B3 for development)

These alternatives are retained for decision audit. **B3 — trusted unprivileged Linux-host Executor invoking local Rootless Podman CLI — is the Owner's selected development target (§9).** Neither B2 nor B1 is a silent fallback; native Linux deployment still requires security validation.

| Criterion | B2: executor + dedicated rootless worker daemon | B1: trusted host executor + narrow IPC |
| --- | --- | --- |
| Privilege boundary | Dedicated non-root daemon reduces host-root surface if genuinely rootless; same-UID and daemon API remain sensitive | Host executor controls Docker daemon; high-consequence trusted code boundary |
| Packaging | Containerized executor with separately provisioned dedicated daemon; **not privileged DinD** | Host service installation and upgrade needed; less pure Compose portability |
| Mount ownership | Must establish daemon-host-real workspace and UID mapping | Direct host path resolution easier, but must still forbid arbitrary bind mounts |
| Windows dev / Linux | Windows Docker Desktop parity uncertain; mandatory capability spike | Host service differs by OS; Linux is target, Windows dev may require simulation |
| Socket exposure | Only executor can access dedicated daemon endpoint, never MCP/Controller/Codex | Only host executor can reach rootful engine; authenticated IPC has strict job schema |
| Failure response | Reject B2 when unsupported; no hidden privilege relaxation | Requires separate Owner approval after reviewed threat mitigation |

A Docker socket is effectively authority over the corresponding daemon even if exposed via a "proxy". Authenticated transport is not a substitute for server-side allowlists.

## 6. Mandatory Executor and production Linux evidence gates (not yet performed end-to-end)

Standalone rootless Podman **P2 metadata** and **P3 synthetic behavior** were reported on Ubuntu WSL2; they are not tests of the actual Controller→Executor boundary, full job lifecycle, or production native Linux. See §9 for the measured-versus-pending distinction.

| Gate | Required future proof | Failure disposition |
| --- | --- | --- |
| Native Linux rootless feasibility | Dedicated unprivileged service account, local rootless Podman CLI, systemd/cgroup v2 delegation, effective resource/seccomp controls, namespaces and nonroot UIDs; record exact versions | Block production approval; redesign or reopen engine comparison |
| WSL2 dev versus native Linux | Re-run real Executor path on a native Linux host/VM; test UID mapping, mount semantics, networking, seccomp, lifecycle and cleanup | Document gap; do not claim production parity from WSL2 |
| Request abuse | Unauthorized caller, replay, changed duplicate id, bad SHA, wrong task/head/round, stale deadline, oversized payload, arbitrary image/mount/flags | Reject before any Podman operation |
| Workspace escape | ../, symlink/junction/reparse, race and hardlink cases; other-project roots, .git/PEM/DB exclusion; world-writable parent threat | Zero unauthorized reads/writes |
| Stage credentials/network | PEM only prepare/publish, model auth only code, none for test; code egress constrained; test/probe no-net; no host socket | Fail-closed, no stage launch |
| Lifecycle | Stop/reap under failure/cancellation/daemon restart, orphan inventory, no double-dispatch, blocked unknown publish and operator-only reconciliation | Never auto-retry external effects |
| Provenance | Pinned image digest, seccomp digest, versioned policy; trusted source snapshot / post-test digest / publish checks | Do not publish or mark DONE |
| Regression | Controller lease/SHA/round/idempotency/timeout and worker GitHub App tests, no duplicated policy paths | No merging until pass |

Evidence should include exact command, OS/engine versions, config digest, behavior observed, expected result, test logs with redaction and owner/reviewer sign-off. Sandbox/probe tests must use disposable local fixtures and separate authorization; no live Controller/worker/daemon invocation under this ADR.

## 7. Decisions held for Owner and subsequent tasks

The Owner has **selected B3 Rootless Podman as the initial development architecture** (§9), superseding the historical B2 preference. If essential behavior cannot be enforced, stop and reopen design/engine evaluation; do not fall back automatically to rootful execution, generic engine sockets, privileged DinD, or the historical B1/B2 variants.

CL-02 owns permissions fixes after threat modeling; CL-12.6 owns topology/workspace/ops plan; CL-07/09 and later separately approved extraction tasks own code/integration; CL-10/11 own DB/data migration. Current source follows Phase 3.1 historical architecture until Owner approves and tests a replacement.

**Tech Lead handoff status:** Proposal authored from repository source and governing Notion decisions; no pytest, Docker, threat penetration, runtime or integration tests were executed for this documentation change. Review and merge authorization remain independent.

## 8. Owner-requested comparison: Rootless Podman versus Rootless Docker (2026-11-06)

**Decision history:** originally REOPENED for comparison; **Owner selected B3 Rootless Podman for development on 2026-11-06** (see §9). The original B2 preference above described a dedicated *rootless Docker* worker daemon. The Owner requested a head-to-head evaluation with **Rootless Podman**, with a separate authorized Codex probe. This appendix supersedes any reading that Docker itself has already been chosen; the trusted *typed Executor* boundary and fail-closed permissions remain mandatory for either runtime. [Authorized isolated probe CL-12.5-P1](https://app.notion.com/p/410f92014abd8149ba53f218478f35cf).

| Decision dimension | Rootless Podman candidate | Dedicated Rootless Docker candidate |
| --- | --- | --- |
| Execution model | Native Podman CLI is daemonless for local run/stop; Podman API service is **optional** but has broad user authority | Long-lived rootless dockerd provides Docker API; the socket has broad daemon authority |
| Privilege | Rootless user namespace and dedicated unprivileged executor user; still dangerous if given arbitrary user-owned mounts | Rootless daemon/container user namespaces and dedicated subordinate ID ranges; still dangerous if given arbitrary daemon control |
| Containerized Executor integration | **Unresolved:** mounting Podman user socket into Executor grants full user-level API, not a narrow job interface. A dedicated host-level rootless CLI Executor with authenticated typed IPC is an alternative that changes the original containerized-executor assumption | Dedicated rootless daemon can be hosted outside Executor container with narrowly restricted access **only from trusted Executor**; Docker-in-Docker rootless recipe using --privileged is rejected |
| Windows dev | Podman Machine backed by WSL2/VM; machine-to-host bind paths, sockets, UID/ACL and default Docker pipe may differ from Docker Desktop. Keep existing Docker context untouched | Existing Docker Desktop Docker API is **not proof of dedicated rootless Engine**; separate rootless Linux/VM engine must be evidenced |
| Linux deployment | Strong candidate with dedicated unprivileged user and rootless Podman, but validate cgroups, seccomp, bind paths and networking | Supported Linux rootless engine, but cgroup enforcement requires compatible cgroup v2/systemd/delegation; validate actual CPU/memory/pid enforcement |
| Current code compatibility | Docker CLI syntax is similar but not guaranteed equivalent: run/rm/ls, --read-only, --user, --mount, --network=none, --security-opt, resource limits, names/filters, cleanup and JSON output require concrete comparison | Less source change to existing CLI, yet changing daemon context/rootless permission and host-real mounts still requires new implementation/tests |
| Supply chain/ops | Separate Podman Machine, image cache, Podman version/flags and rootless service management; rootless API must not be exported to untrusted caller | Dedicated rootless daemon lifecycle, socket/contexts, systemd user services, image storage and cgroup delegation |

**Important security correction:** "Daemonless" does **not** imply a stronger security boundary if an authenticated container can call `podman system service`. Podman's API gives full control as the Podman API user; Docker's rootless socket similarly gives powerful authority in that engine's scope. Neither API is exposed to ChatGPT, MCP, Controller, model code/test or arbitrary job payloads. The executor must translate narrowly typed authenticated jobs into fixed, independently reviewed engine operations.

**Owner-selected B3 for development (not production-approved):** a dedicated **trusted Linux-host Executor process running Rootless Podman CLI as a restricted service account**, called from the containerized Controller over authenticated narrow local IPC. This avoids needing to mount a broad Podman API socket *into another container*, but trades some Compose-only packaging simplicity for a smaller engine API surface. The Owner expressly accepted B3 as the **development target**, supported by Codex-reported P2/P3 feasibility; rootless runtime behavior on production Linux and Executor-specific IPC/lifecycle are not yet validated. This choice is not an automatic fallback or approval to deploy.

### Probe history and outstanding evidence before production

The separate task `CL-12.5-P1` **completed a BLOCKED read-only preflight report** in PR #14, because the required isolated Linux sandbox was not then available. Owner subsequently provisioned Ubuntu-24.04 on WSL2 and authorized isolated P2/P3 probes. Historical P1 expectations below remain as a requirements baseline; P2/P3 results and residual gaps are summarized in §9. Neither stage touched live DB/Compose/credentials or changed the default Docker context. It must report PASS/FAIL/BLOCKED/NOT RUN for real rootless execution, userns/subids, cgroup resource enforcement, seccomp, read-only/cap-drop/no-new-privileges, tmpfs, stage mounts/host path ownership, denied network/secret access, malicious job arguments, source path escapes, kill/reap/crash and replay/idempotency. Both Windows dev and Linux target matter, but **Linux evidence is mandatory before deployment selection**. No privileged DinD, generic engine socket mounts, rootful fallback or unapproved installation is permitted.

The report must identify which existing Docker runtime flags and security policies translate correctly to Podman, what requires code refactoring, and whether B2 or B3 can be safely called by the typed Executor. If rootless implementations cannot be tested without installing tooling or creating a VM, Codex reports the blocker and requests specific Owner approval; it must not fabricate PASS or touch existing live containers.

**Documentation-backed observations, not test results:**
- Podman service authority/security: https://docs.podman.io/en/latest/markdown/podman-system-service.1.html
- Podman rootless and container flags: https://docs.podman.io/en/latest/markdown/podman-run.1.html
- Podman Windows machine: https://podman.io/docs/installation
- Docker rootless mode: https://docs.docker.com/engine/security/rootless/
- Docker rootless cgroups/privileged DinD notes: https://docs.docker.com/engine/security/rootless/tips/

**Current gate (post-merge documentation status):** PR #11 ([security](https://github.com/builacviet/techlead-review-poc/pull/11), squash `e4ce254d24cd46d952ff5d814304c58fde3941b3`), PR #12 ([deployment](https://github.com/builacviet/techlead-review-poc/pull/12), squash `4d7aa10db7992a0735bf095771b258db4d9eb5e6`) and PR #13 ([consolidated ADR](https://github.com/builacviet/techlead-review-poc/pull/13), squash `5cae1207bc40f9c75185017ae9f7f630e9f9feb6`) **are merged design documents**, not Draft PRs or a live deployment. Their historical pre-merge review requirements were fulfilled **only for merging these documents**. Explicit parent CL-12 architecture sign-off, atomic implementation permission, native Linux security validation and production release approvals **are not inferred**. No Executor implementation, existing Docker runtime change or production migration is authorized by this ADR.

## 9. Owner decision: Rootless Podman as the Executor development engine (2026-11-06)

**Decision authority and scope.** The Owner selects **B3: dedicated Linux-host Trusted Executor service running Rootless Podman CLI as a restricted non-root service account** as the initial implementation target for this POC's next development phase. This choice supersedes the B2 rootless-Docker-first preference for development, but does **not** authorize production deployment, code/host/Compose changes, or a generic engine API. Dedicated Rootless Docker remains an alternative if Podman fails mandatory design criteria; a parallel comparator is deferred rather than declared inferior.

**Selected topology:** Controller (container; workflow DB authority remains separate) → authenticated, narrowly typed, bounded IPC → Trusted Executor (unprivileged Linux host systemd service) → **local** Rootless Podman CLI (no remote API) → ephemeral, stage-scoped Worker containers. Controller, MCP, ChatGPT, model code and test cannot reach an engine socket or pass arbitrary Podman CLI arguments. The Executor maps approved workspace IDs to host-real paths and owns engine naming, policy, image digests, mount allowlists and cleanup. The trust boundary must be tested; a host service still has all privileges of its Unix account.

### 9.1 Observed feasibility and provenance (not independently re-executed by the author)

| Stage | Codex-reported evidence | Interpretation / outstanding limits |
| --- | --- | --- |
| P1 ([PR #14](https://github.com/builacviet/techlead-review-poc/pull/14)) | Read-only preflight on Windows initially BLOCKED due to missing approved sandbox. | Correctly reports no engine behavior run; its report remains a historical snapshot, not a failed Podman test. |
| P2 ([Notion](https://app.notion.com/p/410f92014abd816bbb90e114c4ed2dbf)) | On Ubuntu 24.04.5 LTS under WSL2, Podman 4.9.3 `security.rootless=true`, non-remote; non-root UID 1000; subuid/subgid `100000:65536`, cgroup v2/systemd user delegation, overlay on Linux ext4. | Rootless/userns/delegation and backend feasibility metadata. No container behavior tested in P2. |
| P3 ([Notion](https://app.notion.com/p/410f92014abd811cb7c0f851a8969d94)) | Synthetic WSL2 container tests reported actual `cpu.max=25000 100000` and throttle 30/31 periods, `memory.max=64MiB` and `oom_kill=1`, `pids.max=32` and `pids.events max=1`; denied root writes, dropped caps, seccomp filter deny, bounded tmpfs, stage-specific fake secrets, test no-network, timeout ~12.18 s and scoped cleanup. Image `docker.io/library/alpine:3.20` digest was reported and removed. | Supports feasibility **for the cases tested on WSL2 only**. Tech Lead has not directly inspected the retained `REPORT.md`/JSON evidence; P3 remains **In Review**. No generalized guarantee from a single sandbox test. |

**Explicit gaps:** AppArmor and SELinux absent in the WSL2 test environment; `NoNewPrivs=1` was observed but no separate setuid-escalation negative test; private mount propagation was observed but not dynamically tested; sibling/symlink path tests covered only synthetic fixtures. Container-name uniqueness is **not** Executor job idempotency. This is not native Linux production proof, not Docker comparator proof, not authorization for real credentials or existing POC workloads.

### 9.2 Required acceptance tests during separately approved Executor implementation

1. **Authenticated typed IPC:** Controller identity/authorization, schema/nonce/replay rejection, bounded payload, invalid stage/image/mount/flag/UID/SHA/path rejection; no engine API or socket delegated to untrusted callers. Verify each denied request causes **no** container side effect.
2. **End-to-end job idempotency and failure recovery:** same job+payload returns existing status; conflicts rejected; crash before/after container create, timeouts, disconnects, replay, orphan reconciliation, stop and unknown remote publish/commit outcome. Unknown effects remain BLOCKED pending operator review; never duplicate publish.
3. **Workspace containment and stage credentials:** secure host-real mapping, UID/modes and actual bind permissions; traversal/symlink/junction/TOCTOU/hardlink negatives; no mount outside explicit synthetic roots. Test prepare/publish App credentials separately from code model credentials and no-credential/no-network test stage, with fake data. No world-writable stage by default; CL-02 owns the existing chmod concern.
4. **Runtime policy as launched by Executor:** measure effective CPU/memory/pids in the actual worker cgroup/scope, seccomp, read-only root, dropped caps, no-new-privileges (including behavioral negative case), tmpfs, timeout, cleanup and concurrency. No rootful or privileged escape hatch. Confirm mount propagation semantics needed by the actual workspace design, without broad host configuration change.
5. **Operational safety:** pinned image/policy/profile digests, log redaction, isolated worker ownership labels, scoped cleanup/reaper, systemd service lifecycle and restart, auditable error states and safe rollback, version pinning, no direct engine endpoints in Controller/MCP/Codex.
6. **Linux production parity:** before production, repeat the mandatory checks with a dedicated non-root service identity on a supported native Linux host/VM, including actual systemd delegation, rootless storage/network, denied paths and stage policy. WSL2 cannot substitute for this security and deployment gate; characterize AppArmor/SELinux or a reviewed compensating host policy.

**Review and release gates (post-merge):** This ADR is a **merged development design**, not an implementation or P3 proof acceptance. PR #12 reflects the selected Linux-host Podman service, and PR #13 consolidates that decision; their documentation-only Owner merges **do not satisfy** remaining parent/implementation/production security gates. Future changes to this Tech Lead-authored ADR require independent exact-SHA review and Owner merge approval. Separately authorized atomic implementation tasks and negative security tests are required before implementing Executor, changing CURRENT Phase 3.1 runtime, or deploying any component.
