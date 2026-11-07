# CL-12.6 — Linux-portable deployment, workspaces and operations ADR

Status: **Owner-merged architecture documentation** ([CL-12.6 PR #12](https://github.com/builacviet/techlead-review-poc/pull/12), squash `4d7aa10db7992a0735bf095771b258db4d9eb5e6`); B3 Podman is the selected **development design only**, with no runtime deployment/native Linux security validation or general production authorization. Originally drafted: 2026-11-06; merge verified 2026-11-07. Task: https://app.notion.com/p/410f92014abd81f09596f250c674f596

The Owner requested that the Tech Lead draft CL-12.5–12.7 together, overriding their drafting schedule but **not** the review, merge, security or production gates. This file is a standalone documentation-only PR originally branched from main SHA 937d0ba2203bb4cff0a63ea5a6ac3bc094b5137e; CL-12.5 subsequently **merged** in [PR #11](https://github.com/builacviet/techlead-review-poc/pull/11) as `e4ce254d24cd46d952ff5d814304c58fde3941b3`. It now targets its Owner-selected **B3 Rootless Podman CLI** development architecture (a dedicated unprivileged Linux-host Trusted Executor). No deployment, Compose edit, code change, volume operation or database access.

## 1. Goal, observed baseline and choices

Target: a modular monolith in one repository deployed as separate MCP and Controller processes/services on one Linux host. Shared workflow_engine library uses a single physical Linux-local persistent SQLite volume. A dedicated **unprivileged Linux-host Trusted Executor using Rootless Podman CLI** is the **selected development target** under CL-12.5; its activation/deployment remains conditional on separate IPC/security and native-Linux validation. Docker Compose can still package the MCP and Controller containers; **the Executor is not a Compose service** in this B3 design. The documented WSL2 Podman tests establish limited development feasibility, not production parity.

Observed code/config at the recorded base:
- compose.controller.yaml runs only an MCP container (and optional ngrok profile), with Windows-host bind mount ./data/controller -> /state and WFC_DB_PATH=/state/workflow.sqlite3; Controller is *not* a Compose service.
- src/my_wfc/controller.py is a host CLI with durable single-slot dispatch, process lock, BLOCKED reconciliation; src/my_wfc/coder_runtime.py shells out to Docker CLI with absolute operator paths, linked worktrees, stage mounts and secret-specific phases.
- docker/mcp.Dockerfile currently copies my_wfc and order_service; docker/coder.Dockerfile currently COPYs all src. Packages, entrypoints, images and imports have **not** been extracted into the proposed target modules.
- CL-10.1 recorded live Phase 3.1 DB at a Windows bind-mounted location, schema v1; stopped legacy named volume techlead-review-poc_mcp_state remains preserved. CL-12.4 proposes SQLite migration/backup gates, not an executed migration.
- Stage permission broadening stage.chmod(0o777) in coder_runtime.py is a nonblocking CL-02/CL-12.5 ownership issue, not evidence of a demonstrated escape.

Target design draws on [CL-12.2 inventory](CL-12-current-state.md), [CL-12.3 boundaries](CL-12-module-boundaries.md), [CL-12.4 persistence](CL-12-persistence.md), and [CL-12.5 approved Executor security ADR](CL-12-executor-security.md) (merged via PR #11).

## 2. Target topology (conceptual, NOT runnable Compose)

~~~text
ChatGPT Tech Lead
    |
TLS ingress/tunnel (operator-managed) -> [mcp] <--- OAuth verification
                                          |   no Docker socket, no App PEM
                            +-------------+-------------+
                            | workflow_state: SQLite     |  Linux-local persistent volume
                            +-------------+-------------+
                                          |
                                  [controller]  (opt-in profile; off by default)
                                      |  typed authenticated IPC; no engine API/socket
                       [trusted executor]  (Linux host systemd service)
                                      | local Rootless Podman CLI, unprivileged UID
                                      v
                           ephemeral [prepare -> code -> test -> publish]
                              App PEM   Codex auth  no auth   App PEM
                                        allowlisted network    no network
                                          |
                              external GitHub only in trusted stages

Other persistence: operator-managed job workspace/snapshot/session storage,
separate from workflow_state and secrets. Legacy volume untouched.
~~~

Design components:
- **mcp:** FastMCP + OAuth/transport adapters + WorkflowService + SQLite adapter, independently deployed; local/private port or reverse-proxy TLS termination, restricted host binding; no Docker, worker secrets or Controller implementation imports.
- **controller:** separate service, not implicitly started by MCP; one instance under operator-validated locks/leases; starts only by explicit opt-in after DB version/restore and executor gates. No public API; loads only Controller-scoped config and typed executor client.
- **migration:** one-shot, operator-initiated command/profile, disabled by default, **not** a side effect of container startup or service import. Complete exact existing-file/schema/backup/restore checks first, then start authorized services; migration ownership remains CL-11.
- **executor:** **Linux-host** trusted systemd/user service under a dedicated unprivileged account (separate activation gate), outside Compose and distinct from the Controller container. It authenticates and authorizes a narrowly typed Controller IPC; only this host service invokes local rootless Podman CLI under its own Unix identity. No Podman API service or engine socket is mounted into Controller/MCP/Codex; no generic engine create endpoint is exposed. It requires deliberate host package/service-account, cgroup/user-manager, immutable policy and workspace provisioning and **must not** be implicitly started with Compose.
- **worker:** ephemeral single-purpose task containers; trusted prepare/publish get narrowly scoped GitHub App material, untrusted code gets model auth and controlled model egress, test no network/auth; no workflow DB or Git metadata exposed in code/test.
- **ingress/tunnel:** development tunnel explicitly opt-in and separate from production TLS reverse proxy; no hardcoded historical ngrok hostname as an architecture assumption.

Network zones: ingress -> mcp only; private mcp <-> local state through filesystem, no network DB endpoint; Controller -> Linux-host Executor through **private authenticated typed IPC** only, with a strictly scoped Unix socket or otherwise verified local authenticated transport (not a Podman socket); Executor -> **local rootless Podman CLI** under its assigned unprivileged account; trusted worker to GitHub, code only to required model endpoint; test network none. Verify Controller container-to-host IPC mount/UID/permissions and address mapping before implementation. Enforce actual host/engine egress controls rather than relying on the diagram.

## 3. Proposed Compose/operator lifecycle

This is a **requirements table**, not executable YAML; image hashes, Compose profiles and health probes must be validated in later implementation.

| Component | Startup gate | Health/readiness | Restart / secrets |
| --- | --- | --- | --- |
| migration (operator profile) | Manual, maintenance freeze, verified backup, approved DB identity, exact release manifest | Completes successfully or exits nonzero; services remain off on failure | No automatic retries; operator-only DB/backup credentials |
| mcp (default) | Existing intended DB, verified target schema, OAuth config, writable approved volume; never implicit schema creation | Local live ping separate from authenticated readiness/schema/path verification | Restart policy only after startup/restore tests; OAuth config only |
| controller (opt-in profile) | Owner authorization, migration/status gate, **host Executor already installed/ready**, no blocked/unknown state, operator lock | Process alive plus authorized-state checks; **do not probe by dispatching** | Fail closed on uncertainty; no App PEM, no engine/Podman/Docker socket |
| executor (Linux-host service, NOT Compose) | Separate Owner authorization; dedicated non-root service account, protected private IPC, pinned worker image/policy, actual Podman/rootless/cgroup validation | Authenticated bounded read-only status, effective UID/userns/cgroup/seccomp and host service readiness | systemd lifecycle scoped to Executor user, audited restart/reconciliation; no engine socket in Controller, no replay of unknown jobs |
| worker task stage | Valid durable claim, fixed stage/policy, digest and workspace checks, task budget | Bounded stage result, digest, stage-specific controls | Ephemeral, limited resources; no auto-retry after uncertain publish |
| ingress | Operator-approved routing, TLS, allowed Host/origin, external identity provider readiness | Verify route/auth, not mere TCP connect | No secret in image or committed env file |

Compose mechanisms such as healthcheck and depends_on conditions only address declared container dependencies/readiness; they **cannot install or start the Linux-host Executor**; **they do not replace authenticated authorization, version gates or a human maintenance decision**. The controller must be excluded from the default up action, not just depend on a running MCP. See https://docs.docker.com/reference/compose-file/services/ and https://docs.docker.com/compose/how-tos/startup-order/.

Health and restart design: distinguish liveness, readiness and permission to dispatch. Running Controller != permission to dispatch a new job. Restart after a crash must discover any ACTIVE/BLOCKED jobs and their external effects; unknown jobs stay BLOCKED until operator reconciliation, never re-run merely because the container restarted.

Use image digests/pinned dependencies, independent entrypoints and no hidden migrations in startup. **Owner-approved distribution design:** GHCR releases pinned by immutable manifest digest, pre-pulled into the Executor user's rootless Podman local store and launched per stage with `--pull=never`; see §9. Document Compose version tested, service-scoped host-port binds, capabilities, seccomp, read-only roots, tmpfs, logging, secret mounts, volume ownership and resource limits. Policies are build/runtime claims until validated on actual hosts.

## 4. Data volumes, filesystem and workspace provenance

**Workflow state:** one existing, authorized SQLite file on a *Linux-local persistent filesystem* (Docker-managed named volume or explicitly verified Linux-native bind path for MCP/Controller) mounted to MCP and Controller at the same physical location, with correct UID/GID and tested lock/journal behavior. Distinct storage references or identical filenames do not establish shared-file identity. No NFS/SMB multi-host assumption. Worker, executor, untrusted code/test never mount workflow_state. Backup output is stored in a different failure domain with restricted access; preserve legacy named volume independently.

**Operator workspace:** Controller stores only task/workspace IDs, expected source/head SHA, owner authorization and write-path policy, not arbitrary D:/ or /home/... host strings. Executor resolves IDs via operator-owned mapping to **Executor-Linux-host-real** paths, not caller-provided host paths. The Controller container's own `/workspace` path is not meaningful to a separate host Podman CLI unless its *host-side* path, ownership and policy have been explicitly provisioned and tested. Root paths/volume mappings must be explicit and platform-tested; reject caller-controlled bind mounts, junctions/symlinks/reparse-point races, path traversal, Windows reserved names, accidental v2 project roots, and overlapping task workspaces.

**Snapshot chain:**
1. Trusted prepare checks GitHub remote base/head/branch and creates snapshot after approved task/SHA/path checks; secrets/Git metadata stay in trusted private stage.
2. Executor isolates a bounded read/write task snapshot; code sees no .git or PEM, and cannot see other task directories. Stage permissions follow least privilege; investigate the current 0777 staging/source mode under CL-02.
3. Code stage only modifies allowlisted paths, with model auth and vetted model network; test uses credential-free, no-network execution. Preserve pre/post-test source digest and fail on mutation.
4. Trusted publish reconstructs immutable baseline, checks allowable changes and provenance, performs ordinary push (no force push), verifies remote exact SHA, creates draft PR; handoff includes SHA, tests, limits and evidence. No remote GitHub authorization transfer to Controller.
5. Executor reports bounded signed/verified result; uncertain external effect leaves durable BLOCKED state and explicit reconciliation.

**Secret scopes:** OAuth/JWKS settings for MCP only; Controller receives only scoped Executor IPC identity; Executor holds the local rootless Podman CLI authority and operator-owned workspace/policy (never GitHub App PEM or MCP DB); trusted prepare/publish temporarily access App key; code gets model auth only; test gets none. Never copy .env/PEM/DB/auth directories into Docker image layers, logs or generated source snapshots.

**Build isolation:** replace current docker/coder.Dockerfile's COPY src with only worker modules and justified pure worker_contracts; replace MCP broad package copy with only MCP/workflow dependencies. This is a later independently reviewed packaging task. Validate wheel resources (schema.sql/reference fixtures), installed-package imports, SBOM/dependency inventory, cold imports, subprocess entrypoints and that order_service remains stdlib-only. Narrow source copies can break worker imports until shared contract extraction is merged; do not change image and imports in a blind single step.

## 5. WSL2 Rootless Podman development -> native Linux deployment parity matrix

| Area | Mandatory validation | Release condition |
| --- | --- | --- |
| Host path mapping | Executor-host-real workspace IDs on native ext4; bind scope/RO vs RW; symlink/race/hardlink, Windows reparse/path parity where applicable; user-namespace UID/GID mapping and permissions | No arbitrary host bind, secrets or other project exposure; no world-writable staging |
| SQLite durability | Same physical local volume, busy_timeout, BEGIN IMMEDIATE, journal/WAL checkpoint, concurrent MCP/Controller and abrupt termination | No Windows-host/Linux-container cross-OS locking assumption; proven on target |
| Rootless worker engine | Dedicated unprivileged Linux-host service calling **local Rootless Podman CLI**, correct userns/subids, cgroup v2/systemd, effective seccomp/UID/mount/capability/pids/memory/CPU. No API socket into Controller | CL-12.5 §9 mandatory Executor-specific negative tests and native Linux parity pass; fail closed, reopen engine if blocked |
| Network | OAuth ingress, Controller→Executor authenticated private IPC, model-only code egress, GitHub-only trusted egress, test no-net; no Podman or Docker engine API socket exposed to Controller | Negative connectivity/authorization tests from each trust zone and stage |
| Supply chain | Immutable digests, independent wheel resources/image copy rules, no environment-dependent implicit packages | Same validated release manifests and smoke tests |
| Lifecycle | Restart, crash/stop/reap, reconciliation and no automatic dispatch, POC tunnel isolation | Reproducible fail-closed behavior |
| Backup | Online backup or coordinated stop, integrity, isolated restore, time/recovery objective | CL-11 and CL-10 migration/cutover approval before production changes |

Record concrete WSL2/native Linux environments, exact Podman/worker image/policy digest/Compose versions and evidence. P2 verified Podman 4.9.3 rootless metadata; P3 reported cgroup CPU/memory/pids and sandbox behavior on Ubuntu WSL2, **not yet independently inspected** by Tech Lead. A passing WSL2 trial cannot prove native Linux production safety; Windows Docker Desktop is an unrelated existing Phase 3.1 context, not this design's dedicated rootless engine.

## 6. Operations runbook gates (future implementation only)

**Before activation:** Owner-approved maintenance window and deployment plan; review repo SHA/image/manifest, task policies, OAuth/secret scopes, **Linux-host Executor identity/typed IPC/Rootless Podman** privilege gate; run CI/negative security tests; perform SQLite backup and isolated restore, record data-version/row invariants, reconcile active/BLOCKED dispatches. Existing legacy DB remains preserved and non-authoritative.

**Deploy MCP alone:** create validated volume and DB only through separately approved bootstrap/migration procedure; start MCP only, validate authenticated tools/Host binding/schema/health and empty unauthorized dispatch path. No Controller or executor by default. Validate external ingress/tunnel separately.

**Authorize Controller:** only after Owner approval, matching schema and shared physical volume identity, executor capability checks, no uncertain worker effects, explicit opt-in and single dispatch ownership. Start with deliberate manual one-task test in a disposable environment before live dispatch approval.

**Crash/unknown result:** mark/retain BLOCKED; inspect external worker/container/PR/GitHub state, compare exact task/SHA and persisted ledger, stop/reap verified owned containers, obtain operator acknowledgment. Never silently auto-retry job or publish; service liveness cannot clear BLOCKED.

**Backup/recovery/rollback:** stop intake and external effects, snapshot using validated SQLite online API or coordinated stop, restore **to isolated path**, test integrity/schema/data, compare frozen baseline, then Owner approves controlled switch. Restoring an old DB cannot undo GitHub pushes or later accepted writes: reconcile these separately. Never copy active WAL DB blindly, delete volume or run docker compose down -v.

**Retirement:** only after a separately signed retention and recovery decision; no accidental deletion of techlead-review-poc_mcp_state or other projects' volumes.

## 7. Open gates, dependencies and review handoff

- **CL-12.5:** Owner-approved **B3 Rootless Podman development architecture** is merged on `main` (PR #11, squash `e4ce254d24cd46d952ff5d814304c58fde3941b3`); **actual Executor security gates and native Linux production approval are still outstanding**. P3 original REPORT/JSON are pending independent inspection; CL-02 permission issue includes `stage.chmod(0o777)`.
- **CL-12.4 / CL-11:** explicit versioned SQLite migration runner/backup/restore; documented but not implemented.
- **CL-12.7:** consolidate the already merged CL-12.5 ADR and this CL-12.6 deployment ADR only after this PR passes Owner review/merge; consolidated architecture remains Draft until then. Carry forward the §9 GHCR image policy and §10 observability roadmap.
- **Implementation sequencing:** CL-07 regression characterization -> CL-09 namespace-only -> independently authorized package/contract/DI extraction -> CL-11 migration -> CL-10 operational cutover -> remaining CL-01..04/CL-08 security and operations. Executor implementation has extra CL-12.5/12.6 security gates and no silent fallback. CL-05/CL-06 remain deferred.

**Evidence:** Repo/Notion read-only inspection and official Compose/SQLite/Podman/Docker docs; CL-12.5 P2/P3 experiment results are taken from Codex's Owner-forwarded reports, not independently re-executed by this ADR author. GHCR and Grafana observability components in §9–10 are future architectural choices, not deployed or benchmarked. No Compose execution, container build, pytest run, Windows/Linux portability proof, runtime inspection or backup occurred during authoring. Owner must independently review/approve this draft and any PR merge; no automatic next-task release.

## 8. B3 host Executor + Rootless Podman deployment contract (development target only)

**Decision lineage:** [CL-12.5 PR #11](https://github.com/builacviet/techlead-review-poc/pull/11) approved B3 for development, not production. A Linux-host Executor avoids exposing a general Podman engine API in the Controller container. It is a distinct **host trusted component**, with a larger deployment/maintenance surface than a Compose-only design; installation, user service, socket permissions and workspace provisioning must each have explicit operator approval.

### 8.1 Host and container components are deliberately different

| Plane | Placement / ownership | Forbidden linkage |
| --- | --- | --- |
| MCP | Dedicated Compose container, OAuth identity and WorkflowService; approved Linux-local SQLite shared physical DB with Controller only | No executor IPC identity, worker image/Podman engine socket, model credentials, App PEM |
| Controller | **Opt-in Compose container** with workflow DB and a dedicated constrained *Executor IPC client* | No Podman/Docker CLI, host engine socket, raw mount policy, direct worker/secret access |
| Trusted Executor | **Linux host service** run under dedicated unprivileged account and systemd unit; localhost/private authenticated typed IPC; fixed immutable policy and workspace registry | No untrusted process invoking local Podman, no broad socket/API available to Controller, no direct workflow DB, no implicit full-admin/root authority |
| Rootless Podman | Local CLI invoked by host Executor identity only, separate per-user storage and systemd user slice/cgroup | No privileged DinD, generic container-create API, engine REST/TCP, cross-user/container lifecycle control |
| Worker stages | Disposable containers using approved pinned image/policy, isolated stage mounts/network and fixed budgets | No persistent workflow DB or engine socket, no cross-stage or cross-task secrets, no host-root/privileged/host network |

**Important:** mounting a **typed Executor IPC socket** to Controller may be acceptable only after validating socket owner/group/ACL, peer authentication, API request policy, container mount read/write surface and the host namespace mapping. It is **not** equivalent to mounting `/run/user/.../podman/podman.sock` or `/var/run/docker.sock` (prohibited). If Unix socket transport is impractical across host/containers, a separately authenticated private transport requires its own threat model and negative tests; neither method is considered implemented here.

### 8.2 Explicit host provisioning and startup order

1. Operator installs/configures a dedicated non-root Linux service identity (distinct from Windows user/dev shell and other projects), subuid/subgid ranges, rootless Podman and storage on Linux-local filesystem, systemd user manager/linger policy if justified, cgroup v2 delegation, seccomp, bounded resource policy and private log/workspace directories. **No sudo Podman worker execution.** WSL2's `mrviet` account is a development probe identity, not a permanent production service account.
2. Operator configures an authenticated private **typed Executor IPC** endpoint and policy/identity distribution to Controller without granting Podman API control. Restrict allowed Controller identity, nonce/deadlines/replay limits, job state and paths. Verify the Executor can independently reject arbitrary image, mount, flag, stage or job; every rejected operation must leave no container effect.
3. Operator applies the versioned SQLite migration/backup/restore process on the specific approved Linux-local DB **before** enabling Controller dispatch. Read-only identity/schema check cannot itself create a DB. MCP/Controller share exactly one durable DB; Executor/Workers never mount it.
4. Start MCP through its separately approved Compose service, with Controller **off by default**. Start host Executor explicitly through its own systemd/operator procedure, not Compose. Probe health/readiness, policy digest, userns/cgroup and user identity without spawning a task.
5. After separate Owner activation, bring up Controller opt-in service and run one synthetic task before live dispatch; capture IPC denial cases, actual Podman worker cgroups, stage mounts/network/secrets, timeout and crash/unknown-result reconciliation. Fail closed if any required boundary is absent. No default Compose up should auto-launch Controller/Executor.

### 8.3 Versioned workspace and security-policy ownership

- Root directory, task/workspace ID map and stage-specific mounts belong to the **trusted host Executor**; Controller sends only opaque authorized IDs, exact task SHA and allowed profile IDs. Validate host-real paths, open file identities and deny symlink/TOCTOU/hardlink race to another task or host location; never accept model text as a host path. Use native Linux filesystem for worker fixtures/storage, not live Windows `/mnt/c` or `/mnt/d` mounts in development probes.
- The Executor enforces approved immutable worker image digest, pinned seccomp profile checksum, startup resource profile (CPU/mem/pids/swap/tmpfs/timeouts), stage network policy and narrow credential source; these values are **not** caller-editable engine flags. Trusted prepare/publish use separate App auth; code uses model auth; test uses neither. Proof must be stage-specific and negative, not just inspection of requested flags.
- One-way audit of trusted outcome (job/dispatch ID, SHA, image/policy digest, lifecycle timestamps, sanitized error/state) is mandatory. Unknown worker, missing process/cgroup or ambiguous external GitHub outcome ⇒ durable BLOCKED and reconciliation by an authorized operator. A repeated container name error is **not** job-level idempotency.
- The WSL2 P3 experiment reported private root mount propagation, AppArmor/SELinux metadata false, `NoNewPrivs=1` metadata only, and path tests limited to synthetic sibling fixtures. Before production or live use, the **actual Executor launch path** must prove all needed mounts/UIDs/limits, no privilege escalation and a reviewed host confinement policy or compensating controls on **native Linux**. No silent `--privileged`, Docker daemon/rootful fallback, security-opt unconfined or broad host configuration changes.

### 8.4 Separation from currently running POC

Current Phase 3.1 Docker CLI/Compose, the Windows bind-mounted SQLite, existing GitHub App credentials, tunnel and separate Workflow Console v2 resources **remain untouched by this ADR**. A later Owner-authorized cutover must explicitly describe rollback, physical SQLite identity, migration ownership, stopped/reconciled jobs and config/version pinning. A host-installed Executor does not automatically acquire a valid local path to Docker Compose volumes; operators must supply approved mappings and verify them before activation. **No implementation, service installation, new mounts, Compose edit, or production deployment is authorized by this document.**


## 9. Owner decision — GHCR image registry and fast version-pinned Worker startup (2026-11-07)

**Decision:** Use **GitHub Container Registry (GHCR)** as the managed OCI image registry; **Rootless Podman's local per-user image store** on the dedicated Linux-host Executor is the runtime cache. A registry is for building/distributing/auditing approved images; **it is not contacted for every stage**. This design does not require operating a private registry server. The existing Docker Desktop POC is unchanged.

**Release lifecycle**

1. **CI build/test/publish:** Approved GitHub CI builds the Worker image once from a reviewed source commit, runs the defined test and vulnerability/security gates and publishes to GHCR. Associate release version (`v0.1.0` illustrative), full source SHA, build/run provenance, OCI manifest digest, CPU architecture, worker contract and immutable policy/seccomp digests. Define SBOM and attestation/signature verification at the release gate; do not claim these controls already exist.
2. **Version semantics:** Human-friendly tags are discovery labels and may move. The release manifest and executor policy must store the **verified immutable sha256 manifest digest** (and platform); never execute by `latest` or by trusting a mutable tag. No invented digest, no arbitrary image URL or caller-controlled image flag.
3. **Owner-controlled activation:** Only an approved digest and policy version becomes eligible for dispatch. A dedicated, narrowly scoped GHCR pull identity may fetch the approved package; registry credentials are **not mounted into Coder/Test/Prepare/Publish Workers** and are not supplied to Controller or generic IPC jobs.
4. **Pre-pull and readiness:** Before accepting jobs, an approved operator/release step pulls the exact digest into the **Executor's unprivileged Podman UID storage**, verifies the local digest and architecture, and records release readiness. If image is absent, mismatched or cannot be verified, **fail closed**; never auto-fetch `latest` or silently switch engine/user context.
5. **Cached stage execution:** `Prepare → Coder → Test → Publish` creates distinct short-lived containers from the same approved image digest cached locally, with `--pull=never` and stage-specific mounts/credentials/network policy. No build, push, registry pull, or deployment of long-lived stage services occurs on each transition. If stage-specific images are introduced, each requires separate approved digest/contract.
6. **Rollback and GC:** Every job/audit entry records immutable image digest, policy version and source/release SHA. Existing jobs remain bound to their recorded digest. Keep previous approved digests cached for safe rollback until work is reconciled. Remove only authorized and unreferenced image layers after a reviewed retention/GC step; do not globally prune other Podman users' or projects' image stores.

**Implementation tests:** digest/tag substitution and wrong architecture, unauthorized registry access, missing-cache `--pull=never` failure, image trust checks, cold and warm cache, pre-pull/rollback/GC, disk/inode exhaustion, cross-user store isolation and no image/registry credentials in Worker stages. Benchmark separate image distribution time versus cached container startup, stage artifact handoff, and Coder/Test durations (p50/p95). A provisional objective of **under 5% total job time** for stage transition overhead is subject to measurement; **do not trade away stage isolation to satisfy it**.

## 10. Owner decision — Linux Executor observability and staged Grafana rollout (2026-11-07)

**Decision:** Monitoring is required for the host Executor/ephemeral Workers, but deploying the full metrics/logging stack is **not** a prerequisite to the first safe POC. Start with **structured Executor logs to systemd-journald**, bounded health status, basic Prometheus-compatible metrics or a trusted read-only exporter, and a practical incident notification. During staging, adopt **Grafana dashboards**, **Prometheus** for time-series metrics, **Loki** for logs, and **Grafana Alloy** (or equivalently constrained collector) for host metrics/journal collection. These are future implementation choices, not running services.

**Telemetry and least privilege**

- Host health: systemd Executor uptime/restarts/readiness, CPU/RAM/disk/inodes, rootless storage usage, cgroup v2 delegation, resource headroom and capacity.
- Job/stage health: RUNNING/BLOCKED/finished counts and ages, p50/p95 stage duration, startup/cleanup latency, OOM/timeout/PID failures, CPU/memory throttle, orphan count, rejected requests, image digest/cache status, GHCR pre-pull failures, and publish status/ambiguous external effects.
- Logs: structured correlation IDs, job/stage state, release and policy digests and bounded sanitized errors. **Never log GitHub App tokens/PEM, Codex auth, OAuth data, raw source, secret environment or unrestricted workspace paths**; redact Worker stdout and sensitive exception data. Avoid unbounded high-cardinality job IDs in metric labels; keep searchable correlation in access-controlled logs instead.
- Collectors read limited OS/systemd journald and Executor-exported metrics through protected read-only interfaces. **Never mount Podman/Docker socket into Grafana, Prometheus, Alloy, Loki, MCP or Controller solely for monitoring.** Avoid privileged collector, host PID namespace, unrestricted API, remote open metrics port. Protect ingestion/queries with scoped RBAC, TLS/auth where remote, retention and disk quotas; finalize supported versions and capacity in an approved implementation task.

**Alerting and runbook**

| Alert signal | Required response |
| --- | --- |
| Executor unready/down, repeated systemd restart or IPC inaccessible | Stop/hold dispatch, inspect service identity and rootless dependencies; no rootful fallback |
| Old RUNNING or increasing BLOCKED jobs, orphan containers, failed cleanup | Quarantine affected dispatch; reconcile owned jobs and external GitHub effects before retry |
| Repeated OOM, PID/CPU enforcement failures, timeouts, low disk/inodes | Investigate actual cgroup and policy/profile; change limits only with review |
| Missing/untrusted image digest, GHCR pull failure, image-store capacity exhaustion | Fail closed; restore approved digest or roll back via explicit release gate |
| Ambiguous publish result, auth/secret policy violations | Pause affected stage; preserve redacted audit trail; escalate for Owner action |

Alerts initially can be local/operator-facing; optional Telegram integration requires a separately approved scoped token, destination and test. Grafana is **observability only**, never an engine command surface and never a substitute for durable Controller BLOCKED state. Confirm alert delivery, noise thresholds, RBAC, log retention and incident acknowledgement at staging/production gates; no fabricated SLA or threshold values.

**Roadmap and acceptance**

- **CL-12.6:** document GHCR/distribution/version/cache and minimum observability/security expectations only; no CI, registry, host or runtime changes.
- **CL-12.7:** include these decisions in the consolidated ADR, without claiming deployment.
- **Executor implementation (separate Owner authorization):** release-pinning/pre-pull and no-pull negative tests, structured journald logs/metrics, fail-closed release and worker reconciliation, image cache lifecycle and stage startup benchmarks.
- **Staging:** implement/test GHCR CI and controlled release workflow; add Grafana + Prometheus + Loki + Alloy stack as warranted; verify telemetry access boundaries, dashboard/alerts, log redaction, resource limits and native Linux behavior.
- **Production:** explicit Owner sign-off after secure operating model, incident drills, retention/cost and rollout/rollback prove acceptable.
