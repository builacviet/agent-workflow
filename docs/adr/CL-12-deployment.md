# CL-12.6 — Linux-portable deployment, workspaces and operations ADR

Status: **Tech Lead-authored draft / dependency CL-12.5 pending acceptance / no runtime validation**. Date: 2026-11-06. Task: https://app.notion.com/p/410f92014abd81f09596f250c674f596

The Owner requested that the Tech Lead draft CL-12.5–12.7 together, overriding their drafting schedule but **not** the review, merge, security or production gates. This file is a standalone documentation-only PR based on main SHA 937d0ba2203bb4cff0a63ea5a6ac3bc094b5137e. It assumes the candidate CL-12.5 design in [draft PR #11](https://github.com/builacviet/techlead-review-poc/pull/11); reconcile any changes to that design before accepting/merging CL-12.6. No deployment, Compose edit, code change, volume operation or database access.

## 1. Goal, observed baseline and choices

Target: a modular monolith in one repository deployed as separate MCP and Controller processes/services on one Linux host. Shared workflow_engine library uses a single physical Linux-local persistent SQLite volume. A dedicated trusted executor and rootless worker daemon are **conditional on CL-12.5's security/platform gate**. Windows Docker Desktop is the development platform, not proof of production parity.

Observed code/config at the recorded base:
- compose.controller.yaml runs only an MCP container (and optional ngrok profile), with Windows-host bind mount ./data/controller -> /state and WFC_DB_PATH=/state/workflow.sqlite3; Controller is *not* a Compose service.
- src/my_wfc/controller.py is a host CLI with durable single-slot dispatch, process lock, BLOCKED reconciliation; src/my_wfc/coder_runtime.py shells out to Docker CLI with absolute operator paths, linked worktrees, stage mounts and secret-specific phases.
- docker/mcp.Dockerfile currently copies my_wfc and order_service; docker/coder.Dockerfile currently COPYs all src. Packages, entrypoints, images and imports have **not** been extracted into the proposed target modules.
- CL-10.1 recorded live Phase 3.1 DB at a Windows bind-mounted location, schema v1; stopped legacy named volume techlead-review-poc_mcp_state remains preserved. CL-12.4 proposes SQLite migration/backup gates, not an executed migration.
- Stage permission broadening stage.chmod(0o777) in coder_runtime.py is a nonblocking CL-02/CL-12.5 ownership issue, not evidence of a demonstrated escape.

Target design draws on [CL-12.2 inventory](CL-12-current-state.md), [CL-12.3 boundaries](CL-12-module-boundaries.md), [CL-12.4 persistence](CL-12-persistence.md), and [CL-12.5 draft](CL-12-executor-security.md) (the last link resolves only after its separate approved PR merges).

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
                                      |  typed authenticated job API; no Docker API
                               [trusted executor]  (separately gated)
                                      | private dedicated rootless worker engine
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
- **executor:** conditional internal service, private authenticated IPC, only component permitted to reach dedicated worker daemon. May require host-installed daemon provisioning; never imply running all services in Compose magically creates a safe rootless daemon.
- **worker:** ephemeral single-purpose task containers; trusted prepare/publish get narrowly scoped GitHub App material, untrusted code gets model auth and controlled model egress, test no network/auth; no workflow DB or Git metadata exposed in code/test.
- **ingress/tunnel:** development tunnel explicitly opt-in and separate from production TLS reverse proxy; no hardcoded historical ngrok hostname as an architecture assumption.

Network zones: ingress -> mcp only; private mcp <-> local state through filesystem, no network DB endpoint; Controller -> executor authenticated private control plane; executor -> dedicated worker daemon only; trusted worker to GitHub, code only to required model endpoint; test network none. Enforce with actual host/engine egress controls rather than only a diagram.

## 3. Proposed Compose/operator lifecycle

This is a **requirements table**, not executable YAML; image hashes, Compose profiles and health probes must be validated in later implementation.

| Component | Startup gate | Health/readiness | Restart / secrets |
| --- | --- | --- | --- |
| migration (operator profile) | Manual, maintenance freeze, verified backup, approved DB identity, exact release manifest | Completes successfully or exits nonzero; services remain off on failure | No automatic retries; operator-only DB/backup credentials |
| mcp (default) | Existing intended DB, verified target schema, OAuth config, writable approved volume; never implicit schema creation | Local live ping separate from authenticated readiness/schema/path verification | Restart policy only after startup/restore tests; OAuth config only |
| controller (opt-in profile) | Owner authorization, migration/status gate, executor ready, no blocked/unknown state, operator lock | Process alive plus authorized-state checks; **do not probe by dispatching** | Fail closed on uncertainty; no PEM, no host-wide daemon socket |
| executor (conditional opt-in) | CL-12.5 security/platform acceptance, narrow IPC auth, pinned worker image/policy | Authenticated local read-only status check, daemon capability proof | Explicit orphan cleanup and no automatic generic job replay; restricted engine access |
| worker task stage | Valid durable claim, fixed stage/policy, digest and workspace checks, task budget | Bounded stage result, digest, stage-specific controls | Ephemeral, limited resources; no auto-retry after uncertain publish |
| ingress | Operator-approved routing, TLS, allowed Host/origin, external identity provider readiness | Verify route/auth, not mere TCP connect | No secret in image or committed env file |

Compose mechanisms such as healthcheck and depends_on conditions only address declared dependencies/readiness; **they do not replace authenticated authorization, version gates or a human maintenance decision**. The controller must be excluded from the default up action, not just depend on a running MCP. See https://docs.docker.com/reference/compose-file/services/ and https://docs.docker.com/compose/how-tos/startup-order/.

Health and restart design: distinguish liveness, readiness and permission to dispatch. Running Controller != permission to dispatch a new job. Restart after a crash must discover any ACTIVE/BLOCKED jobs and their external effects; unknown jobs stay BLOCKED until operator reconciliation, never re-run merely because the container restarted.

Use image digests/pinned dependencies, independent entrypoints and no hidden migrations in startup. Document Compose version tested, service-scoped host-port binds, capabilities, seccomp, read-only roots, tmpfs, logging, secret mounts, volume ownership and resource limits. Policies are build/runtime claims until validated on actual hosts.

## 4. Data volumes, filesystem and workspace provenance

**Workflow state:** one existing, authorized SQLite file on a Docker-managed *Linux-local filesystem* mounted to MCP and Controller at the same physical location, with correct UID/GID and tested lock/journal behavior. Distinct storage references or identical filenames do not establish shared-file identity. No NFS/SMB multi-host assumption. Worker, executor, untrusted code/test never mount workflow_state. Backup output is stored in a different failure domain with restricted access; preserve legacy named volume independently.

**Operator workspace:** Controller stores only task/workspace IDs, expected source/head SHA, owner authorization and write-path policy, not arbitrary D:/ or /home/... host strings. Executor resolves IDs via operator-owned mapping to daemon-host-real paths. The container's own /workspace path is not necessarily meaningful on the Docker daemon host. Root paths/volume mappings must be explicit and platform-tested; reject caller-controlled bind mounts, junctions/symlinks/reparse-point races, path traversal, Windows reserved names, accidental v2 project roots, and overlapping task workspaces.

**Snapshot chain:**
1. Trusted prepare checks GitHub remote base/head/branch and creates snapshot after approved task/SHA/path checks; secrets/Git metadata stay in trusted private stage.
2. Executor isolates a bounded read/write task snapshot; code sees no .git or PEM, and cannot see other task directories. Stage permissions follow least privilege; investigate the current 0777 staging/source mode under CL-02.
3. Code stage only modifies allowlisted paths, with model auth and vetted model network; test uses credential-free, no-network execution. Preserve pre/post-test source digest and fail on mutation.
4. Trusted publish reconstructs immutable baseline, checks allowable changes and provenance, performs ordinary push (no force push), verifies remote exact SHA, creates draft PR; handoff includes SHA, tests, limits and evidence. No remote GitHub authorization transfer to Controller.
5. Executor reports bounded signed/verified result; uncertain external effect leaves durable BLOCKED state and explicit reconciliation.

**Secret scopes:** OAuth/JWKS settings for MCP only; Controller receives only executor IPC identity; Executor receives dedicated engine and workspace policy (never GitHub App PEM or MCP DB); trusted prepare/publish temporarily access App key; code gets model auth only; test gets none. Never copy .env/PEM/DB/auth directories into Docker image layers, logs or generated source snapshots.

**Build isolation:** replace current docker/coder.Dockerfile's COPY src with only worker modules and justified pure worker_contracts; replace MCP broad package copy with only MCP/workflow dependencies. This is a later independently reviewed packaging task. Validate wheel resources (schema.sql/reference fixtures), installed-package imports, SBOM/dependency inventory, cold imports, subprocess entrypoints and that order_service remains stdlib-only. Narrow source copies can break worker imports until shared contract extraction is merged; do not change image and imports in a blind single step.

## 5. Windows Docker Desktop dev -> Linux target parity matrix

| Area | Mandatory validation | Release condition |
| --- | --- | --- |
| Host path mapping | Daemon-host workspace IDs, symlink/reparse, Windows path delimiters vs POSIX paths, UID/GID ownership and ACL | No arbitrary host bind, secrets or v2 path exposure |
| SQLite durability | Same physical local volume, busy_timeout, BEGIN IMMEDIATE, journal/WAL checkpoint, concurrent MCP/Controller and abrupt termination | No Windows-host/Linux-container cross-OS locking assumption; proven on target |
| Worker daemon | True dedicated rootless daemon without privileged DinD; seccomp/UID/mount/pid/memory/CPU and namespace enforcement | CL-12.5 negative gate passed; otherwise Owner B1 fallback decision |
| Network | OAuth ingress, model-only code egress, GitHub-only trusted egress, test no-net, no exposed internal Docker API | Network deny tests from each stage |
| Supply chain | Immutable digests, independent wheel resources/image copy rules, no environment-dependent implicit packages | Same validated release manifests and smoke tests |
| Lifecycle | Restart, crash/stop/reap, reconciliation and no automatic dispatch, POC tunnel isolation | Reproducible fail-closed behavior |
| Backup | Online backup or coordinated stop, integrity, isolated restore, time/recovery objective | CL-11 and CL-10 migration/cutover approval before production changes |

Record concrete Windows/Linux test environments, exact image/digest/Compose/Docker versions and evidence. A passing Docker Desktop trial alone does not prove Linux deployability.

## 6. Operations runbook gates (future implementation only)

**Before activation:** Owner-approved maintenance window and deployment plan; review repo SHA/image/manifest, task policies, OAuth/secret scopes, executor engine privilege gate; run CI/negative security tests; perform SQLite backup and isolated restore, record data-version/row invariants, reconcile active/BLOCKED dispatches. Existing legacy DB remains preserved and non-authoritative.

**Deploy MCP alone:** create validated volume and DB only through separately approved bootstrap/migration procedure; start MCP only, validate authenticated tools/Host binding/schema/health and empty unauthorized dispatch path. No Controller or executor by default. Validate external ingress/tunnel separately.

**Authorize Controller:** only after Owner approval, matching schema and shared physical volume identity, executor capability checks, no uncertain worker effects, explicit opt-in and single dispatch ownership. Start with deliberate manual one-task test in a disposable environment before live dispatch approval.

**Crash/unknown result:** mark/retain BLOCKED; inspect external worker/container/PR/GitHub state, compare exact task/SHA and persisted ledger, stop/reap verified owned containers, obtain operator acknowledgment. Never silently auto-retry job or publish; service liveness cannot clear BLOCKED.

**Backup/recovery/rollback:** stop intake and external effects, snapshot using validated SQLite online API or coordinated stop, restore **to isolated path**, test integrity/schema/data, compare frozen baseline, then Owner approves controlled switch. Restoring an old DB cannot undo GitHub pushes or later accepted writes: reconcile these separately. Never copy active WAL DB blindly, delete volume or run docker compose down -v.

**Retirement:** only after a separately signed retention and recovery decision; no accidental deletion of techlead-review-poc_mcp_state or other projects' volumes.

## 7. Open gates, dependencies and review handoff

- **CL-12.5:** rootless Executor design remains conditional until negative/security/platform validation and Owner acceptance; CL-02 permission issue includes stage.chmod(0o777).
- **CL-12.4 / CL-11:** explicit versioned SQLite migration runner/backup/restore; documented but not implemented.
- **CL-12.7:** consolidate this ADR only after PR #11 and this CL-12.6 draft are reconciled; neither draft claims a final architecture sign-off.
- **Implementation sequencing:** CL-07 regression characterization -> CL-09 namespace-only -> independently authorized package/contract/DI extraction -> CL-11 migration -> CL-10 operational cutover -> remaining CL-01..04/CL-08 security and operations. Executor implementation has extra CL-12.5/12.6 security gates and no silent fallback. CL-05/CL-06 remain deferred.

**Evidence:** Repo/Notion read-only inspection and official Compose/SQLite/Docker docs. No Compose execution, container build, pytest run, Windows/Linux portability proof, runtime inspection or backup occurred during authoring. Owner must independently review/approve this draft and any PR merge; no automatic next-task release.