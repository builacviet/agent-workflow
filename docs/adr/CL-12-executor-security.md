# CL-12.5 — Trusted Executor security ADR and platform decision gate

Status: **Tech Lead-authored proposal / unvalidated / pending independent Owner review**. Date: 2026-11-06. Task: https://app.notion.com/p/410f92014abd816996ace95343795c15

Base: 937d0ba2203bb4cff0a63ea5a6ac3bc094b5137e. Scope: **this document only**. No code, Docker, credentials, live DB, Controller or host operations. CL-12 final design sign-off and implementation authorization are separate. This replaces neither the CL-02 permissions work nor the CL-11 schema plan.

## 1. Decision and evidence boundary

**Original conditional baseline B2 (now reopened for Podman comparison in §8):** Controller container -> authenticated, narrowly typed Trusted Executor interface -> executor container -> **dedicated rootless worker Docker daemon**, isolated from the host-wide daemon. The daemon must be provisioned without privileged Docker-in-Docker (DinD), must not expose a generic container-create API to Controller/MCP/Codex, and must pass all platform/security gates below. The executor is trusted and still requires strong internal authentication, allowlists, ownership and audit. Rootless reduces daemon privileges; it does not prove sandbox isolation.

**Fail-closed fallback B1:** a minimal trusted host executor with authenticated local-only IPC and strictly limited Docker actions **only after Owner explicitly chooses it**, following a failed or impractical B2 feasibility evaluation. No automatic fallback to rootful daemon access or privileged DinD. A rootful generic Docker socket proxy is not an adequate boundary.

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
| ChatGPT client / MCP ingress | OAuth-scoped review requests; public ping/info | Docker daemon, host paths, GitHub App PEM, Codex auth material, direct DB/admin |
| MCP service | Auth verification + workflow repository on approved DB | Worker images, daemon/socket, arbitrary job execution, app PEM |
| Controller | Durable dispatch ownership, checks on task/round/SHA, bounded typed job submission | Docker command creation, arbitrary host mounts, GitHub App credentials, direct host daemon |
| Executor (trusted) | Strictly authenticated request parser, local job policy, provisioning/stop/reap and host-real workspace mapping | Dynamic policy from prompt, arbitrary image/mount/flags, unrestricted TCP API, database schema mutation |
| Dedicated worker daemon | Only approved ephemeral worker lifecycle in dedicated rootless context | Host-wide Docker socket, privileged containers, host-wide mounts/devices |
| Trusted prepare/publish | Scoped short-lived App key/token, network access to GitHub, Git metadata and validated snapshot/publish | Workflow DB, generic Docker controls or persistent unscoped credentials |
| Untrusted Codex code stage | Task snapshot/write allowlist, model authentication and bounded model network egress if validated | .git, App PEM/token, workflow DB, Docker socket, host paths, executor secrets |
| Untrusted pytest/test stage | Test snapshot and ephemeral nonsecret scratch | Network, Codex auth, App PEM, .git, DB, daemon |
| order_service | Independent stdlib example | Any workflow, executor or worker dependency |

Protected assets: GitHub App PEM and installation token; model auth volume; private source and Git object provenance; task source/output allowlist; workflow SQLite and dispatch ledger; container engine socket; host workspaces and other projects (including Workflow Console v2); worker result.json; access logs, backups and configuration.

Explicit boundary transitions: authenticated MCP -> domain workflow (not job execution); trusted Controller -> typed executor (not Docker API); trusted prepare -> sanitized code snapshot; code -> credential-free no-network test; verified test/digest -> trusted publish. No LLM-generated text, task metadata, symlink, worker output or container name is inherently trusted.

## 3. Minimal executor API contract (proposal, not implementation)

The caller is a **specific authenticated Controller identity**, not a ChatGPT task/tool or Codex process. Private Unix socket or mutually authenticated local transport; reject unauthenticated/duplicate/expired credentials and restrict network reachability. No endpoint that accepts raw Docker CLI args.

Requests are schema-versioned and strictly validated:
- submit(job_id, dispatch_id, task_id, input_sha, approved_image_digest, workspace_id, stage, deadline, resource_profile_id, policy_version, request_nonce)
- get_status(job_id)
- stop(job_id)
- reconcile(job_id), operator-only acknowledgment after inspecting external effects.

Job ID and dispatch ID must bind to the durable DB assignment and exact full SHA; executor separately checks an authenticated authorization claim and persisted idempotency record, not just syntactic validity. stage is an enum: prepare, code, test, publish (probe only under explicitly authorized security validation). Client cannot select mounts, hostname, device, network_mode, seccomp path, UID or arbitrary flags; executor resolves a versioned policy from **operator-owned config**, enforces approved image by immutable digest, fixed stage transitions and bounded CPU/memory/pids/timeouts/output.

Return a bounded typed result: job_id, request digest, state, monotonic lifecycle timestamps, phase artifact digest, reason code and externally verifiable immutable publish reference where applicable. Reject oversized/unknown fields, paths, unapproved image tags, path traversal, extra Docker options, invalid SHA, stale claim or deadline and conflicting duplicate job IDs. Log correlation IDs without secret values. Remote Git truth is verified by the worker and independently by Tech Lead; executor does not invent remote freshness.

State machine: NEW -> PREPARING -> CODING -> TESTING -> PUBLISHING -> DONE; failures/timeout/host restart/unknown remote effect -> BLOCKED until operator reconciliation. Repeated identical submit must return existing status, never launch a second job. Changed payload with same id must fail. Publish must not run unless code/test succeeded, source digest unchanged and the deadline/authorization still hold. Retain existing two-round review policy independently; executor cannot approve a PR or increment review rounds.

## 4. Capability and filesystem policy

- Validate workspace_id against Controller-provisioned registry, then resolve daemon-host-real directories under one approved workspace root. Forbid arbitrary caller host paths, bind-source overrides, relative escapes, symlink/junction/reparse traversal, hard-link surprises and mounts into other task or v2 project roots. Recheck target identity before each stage/mount.
- Use least-privilege ownership and an explicit cross-UID mapping; no world-writable stage by default. Investigate stage.chmod(0o777) and current worker source chmod before any permission refactor. On Windows Docker Desktop and Linux separately verify effective ACL/mode, not just displayed bits.
- Code/test get a **sealed source snapshot without .git** or secrets; prepare/publish separately mount only the privileged material they need, for bounded lifespan. Model auth belongs to code only; test gets neither model nor App credentials. Never log keys, tokens or worker stdout containing secrets.
- Use read-only container root, no-new-privileges, all caps dropped, nonroot UID, read-only mounts except explicitly required scratch, independently validated seccomp/AppArmor, pids/CPU/memory/tmpfs and a hard time budget. An operator-maintained profile checksum and image digest are required; fail closed when unavailable. No --privileged, host PID/network, extra devices or discretionary security-opt flags.
- Network: test/probe **none**; code may require model endpoint egress and must be constrained to verified destination/port/DNS policy, not assumed offline; trusted prepare/publish need approved GitHub endpoints only. Reject any request for unrestricted host networking or arbitrary endpoint.
- Worker process, signal and orphan cleanup must be checked after timeout, cancellation, Controller or executor crash. An unverified cleanup leaves dispatch BLOCKED; do not retry a possibly published task.

## 5. B2 versus B1

| Criterion | B2: executor + dedicated rootless worker daemon | B1: trusted host executor + narrow IPC |
| --- | --- | --- |
| Privilege boundary | Dedicated non-root daemon reduces host-root surface if genuinely rootless; same-UID and daemon API remain sensitive | Host executor controls Docker daemon; high-consequence trusted code boundary |
| Packaging | Containerized executor with separately provisioned dedicated daemon; **not privileged DinD** | Host service installation and upgrade needed; less pure Compose portability |
| Mount ownership | Must establish daemon-host-real workspace and UID mapping | Direct host path resolution easier, but must still forbid arbitrary bind mounts |
| Windows dev / Linux | Windows Docker Desktop parity uncertain; mandatory capability spike | Host service differs by OS; Linux is target, Windows dev may require simulation |
| Socket exposure | Only executor can access dedicated daemon endpoint, never MCP/Controller/Codex | Only host executor can reach rootful engine; authenticated IPC has strict job schema |
| Failure response | Reject B2 when unsupported; no hidden privilege relaxation | Requires separate Owner approval after reviewed threat mitigation |

A Docker socket is effectively authority over the corresponding daemon even if exposed via a "proxy". Authenticated transport is not a substitute for server-side allowlists.

## 6. Mandatory evidence and negative-test gate (not yet performed)

| Gate | Required future proof | Failure disposition |
| --- | --- | --- |
| Linux rootless feasibility | Dedicated rootless daemon with no privileged DinD, correct cgroup/resource accounting, seccomp/AppArmor, namespaces and nonroot UIDs; capture exact versions | Block B2; Owner selects reviewed B1 or redesign |
| Docker Desktop parity | Same typed job, UID mapping, mount semantics, worker networking, seccomp and cleanup results on supported Windows Docker Desktop host | Document gap; do not claim portable |
| Request abuse | Unauthorized caller, replay, changed duplicate id, bad SHA, wrong task/head/round, stale deadline, oversized payload, arbitrary image/mount/flags | Reject before Docker operation |
| Workspace escape | ../, symlink/junction/reparse, race and hardlink cases; other-project roots, .git/PEM/DB exclusion; world-writable parent threat | Zero unauthorized reads/writes |
| Stage credentials/network | PEM only prepare/publish, model auth only code, none for test; code egress constrained; test/probe no-net; no host socket | Fail-closed, no stage launch |
| Lifecycle | Stop/reap under failure/cancellation/daemon restart, orphan inventory, no double-dispatch, blocked unknown publish and operator-only reconciliation | Never auto-retry external effects |
| Provenance | Pinned image digest, seccomp digest, versioned policy; trusted source snapshot / post-test digest / publish checks | Do not publish or mark DONE |
| Regression | Controller lease/SHA/round/idempotency/timeout and worker GitHub App tests, no duplicated policy paths | No merging until pass |

Evidence should include exact command, OS/engine versions, config digest, behavior observed, expected result, test logs with redaction and owner/reviewer sign-off. Sandbox/probe tests must use disposable local fixtures and separate authorization; no live Controller/worker/daemon invocation under this ADR.

## 7. Decisions held for Owner and subsequent tasks

B2 is a **preference with conditional gate**, not an approved operational design. If rootless feasibility or Docker Desktop parity fails, stop; submit B1 with specific rootful privilege risk/compensating controls for Owner approval. No unsafe fallback by automation.

CL-02 owns permissions fixes after threat modeling; CL-12.6 owns topology/workspace/ops plan; CL-07/09 and later separately approved extraction tasks own code/integration; CL-10/11 own DB/data migration. Current source follows Phase 3.1 historical architecture until Owner approves and tests a replacement.

**Tech Lead handoff status:** Proposal authored from repository source and governing Notion decisions; no pytest, Docker, threat penetration, runtime or integration tests were executed for this documentation change. Review and merge authorization remain independent.

## 8. Owner-requested comparison: Rootless Podman versus Rootless Docker (2026-11-06)

**Decision status: REOPENED / PENDING EVIDENCE.** The original B2 preference above described a dedicated *rootless Docker* worker daemon. The Owner requested a head-to-head evaluation with **Rootless Podman**, with a separate authorized Codex probe. This appendix supersedes any reading that Docker itself has already been chosen; the trusted *typed Executor* boundary and fail-closed permissions remain mandatory for either runtime. [Authorized isolated probe CL-12.5-P1](https://app.notion.com/p/410f92014abd8149ba53f218478f35cf).

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

**Additional candidate, not silently approved — B3:** a dedicated **trusted Linux-host Executor process running Rootless Podman CLI as a restricted service account**, called from the containerized Controller over authenticated narrow local IPC. This avoids needing to mount a broad Podman API socket *into another container*, but trades some Compose-only packaging simplicity for a smaller engine API surface. Treat B3 as an architecture deviation that requires explicit Owner acceptance after probe; it is not an automatic fallback or a claim that rootless execution has been validated.

### Required Codex evidence before selection

The separate task `CL-12.5-P1` runs *only on a disposable isolated environment* with read-only host preflight, no live DB/Container/Compose/credentials and no changes to the user's default Docker context. It must report PASS/FAIL/BLOCKED/NOT RUN for real rootless execution, userns/subids, cgroup resource enforcement, seccomp, read-only/cap-drop/no-new-privileges, tmpfs, stage mounts/host path ownership, denied network/secret access, malicious job arguments, source path escapes, kill/reap/crash and replay/idempotency. Both Windows dev and Linux target matter, but **Linux evidence is mandatory before deployment selection**. No privileged DinD, generic engine socket mounts, rootful fallback or unapproved installation is permitted.

The report must identify which existing Docker runtime flags and security policies translate correctly to Podman, what requires code refactoring, and whether B2 or B3 can be safely called by the typed Executor. If rootless implementations cannot be tested without installing tooling or creating a VM, Codex reports the blocker and requests specific Owner approval; it must not fabricate PASS or touch existing live containers.

**Documentation-backed observations, not test results:**
- Podman service authority/security: https://docs.podman.io/en/latest/markdown/podman-system-service.1.html
- Podman rootless and container flags: https://docs.podman.io/en/latest/markdown/podman-run.1.html
- Podman Windows machine: https://podman.io/docs/installation
- Docker rootless mode: https://docs.docker.com/engine/security/rootless/
- Docker rootless cgroups/privileged DinD notes: https://docs.docker.com/engine/security/rootless/tips/

**Updated gate:** Do not approve/merge PR #11 as a *final runtime selection* until probe outcome and the Owner's explicit engine/Executor boundary decision are incorporated. PR #12 (deployment) and #13 (consolidated ADR) must be reconciled to that decision before final acceptance; both currently remain Draft. No implementation or production migration has been authorized.
