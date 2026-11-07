# CL-12.7 — Consolidated architecture decision record and implementation handoff

**Status: Owner-merged consolidated CL-12 architecture documentation** ([PR #13](https://github.com/builacviet/techlead-review-poc/pull/13), squash `5cae1207bc40f9c75185017ae9f7f630e9f9feb6` on 2026-11-07). **Separate parent CL-12 final sign-off, implementation task authority and production security approval are NOT established by this merge.** Updated: 2026-11-07. Task: https://app.notion.com/p/410f92014abd810284d7e98107016b61

This document reconciles **CL-12.1–CL-12.6** against the actual merged architecture ADRs: [PR #11 — CL-12.5 security](https://github.com/builacviet/techlead-review-poc/pull/11), squash commit `e4ce254d24cd46d952ff5d814304c58fde3941b3`; and [PR #12 — CL-12.6 deployment](https://github.com/builacviet/techlead-review-poc/pull/12), squash commit `4d7aa10db7992a0735bf095771b258db4d9eb5e6`. The Owner selected **Rootless Podman for development**, **GHCR for image version/distribution**, and a **staged Executor observability approach**. This is a documentation-only PR and does not amend runtime behavior or retrospectively certify P2/P3 evidence.

## 1. Inputs, traceability and evidence

| Subtask | Governing artifact | State for this synthesis |
| --- | --- | --- |
| CL-12.1 | [Task/review contract](../tasks/CL-12.md) | Owner Approved/merged previously |
| CL-12.2 | [Current 24-module inventory and trust boundaries](CL-12-current-state.md); PR #8 | Approved/merged |
| CL-12.3 | [Target package/DI/DRY boundaries](CL-12-module-boundaries.md); PR #9 | Approved/merged |
| CL-12.4 | [SQLite persistence, migration and recovery](CL-12-persistence.md); PR #10 | Approved/merged |
| CL-12.5 | [Trusted Executor security ADR](CL-12-executor-security.md); [PR #11](https://github.com/builacviet/techlead-review-poc/pull/11), squash `e4ce254d24cd46d952ff5d814304c58fde3941b3` | **Merged**: B3 Rootless Podman **development architecture**, security gates deferred |
| CL-12.6 | [Linux deployment, workspaces and operations](CL-12-deployment.md); [PR #12](https://github.com/builacviet/techlead-review-poc/pull/12), squash `4d7aa10db7992a0735bf095771b258db4d9eb5e6` | **Merged**: host Executor topology, GHCR/versioning, staged Grafana |
| CL-12.7 | This consolidated ADR; [PR #13](https://github.com/builacviet/techlead-review-poc/pull/13) | **Owner-merged** as `5cae1207bc40f9c75185017ae9f7f630e9f9feb6`; separate parent CL-12 sign-off/implementation gates remain open |

Other governing sources: [parent CL-12 Notion](https://app.notion.com/p/410f92014abd81cdbceded689b7d4945), [CL-10.1 DB inventory](https://app.notion.com/p/410f92014abd8186b104d65641e0c9ca), `AGENTS.md`, `docs/architecture.md`, `docs/review-policy.md`, `docs/handoff.md`. Historical Notion or draft descriptions naming a selected dedicated rootless Docker daemon are **superseded for this development phase** by PR #11 and #12.

**Evidence classes:** (a) **Existing observed baseline** = source/config and dated CL-10.1 audit, not production proof; (b) **Owner-approved development design** = decisions merged in PR #11/#12; (c) **Codex-reported probe** = WSL2 P2/P3 results, with P3 original REPORT/JSON still pending independent Tech Lead inspection; (d) **Acceptance gate** = behavior to test only during separately authorized implementation/staging/native Linux rollout. Do not treat a merged document as a passed integration/security test.

## 2. Target deployment and trust boundary

```text
ChatGPT / authorized Owner
         |
     TLS + OAuth
         v
+------------------------ Docker Compose on Linux host -----------------------+
|  MCP service (OAuth, workflow API)                 Controller (opt-in only) |
|       |                                                  |                  |
|       +-- shared, SAME physical Linux-local SQLite DB ---+                  |
|                                                          |                  |
+----------------------------------------------------------|------------------+
                                                           |
                                  authenticated, strictly typed bounded IPC
                                  no direct Podman/Docker engine socket/API
                                                           |
                                +--------------------------v-----------------+
                                | Trusted Executor (Linux HOST systemd)       |
                                | dedicated UNPRIVILEGED user / policies     |
                                | local rootless Podman CLI, image cache     |
                                +--------------------------|-----------------+
                                                           |
                                          disposable stage containers
                                                           |
                          Prepare -> Coder -> Test -> Publish
                         trusted   untrusted untrusted trusted
                         App Git    model auth  no auth    App Git
                         source     sandboxed   offline    commit/push/PR
```

- **Controller** owns durable dispatch orchestration, bounded requests and state, but must not directly invoke any Podman/Docker CLI/API, mount engine sockets, accept arbitrary image/mount flags or receive GitHub App secrets.
- **Trusted Executor** is a distinct Linux-host systemd service, not a Compose container. The Controller-to-Executor **transport implementation remains to be selected/validated**: an authenticated private Unix socket is a candidate subject to container-to-host mount, peer identity/ACL and namespace tests; a private authenticated alternative requires its own review. The **typed IPC contract**, caller authorization, request policy and replay/idempotency checks are mandatory regardless of transport. No generic engine API exposure.
- **Executor→Worker** uses only local **Rootless Podman CLI** under the dedicated non-root user (no general worker HTTP management endpoint). Executor owns fixed image digest, stage profiles, workspace mappings, lifecycle, typed result verification and selective cleanup.
- **Prepare/Coder/Test/Publish are four logical stages** with fresh bounded containers and different mount/network/credential policies, **not four always-on services**. Containers can reuse the **same pre-cached immutable Worker image**; no rebuild or registry fetch between stages. Prepare obtains verified source snapshot, Coder edits within approved allowlist, Test runs without model/App credentials and with no network, **trusted Publish validates immutable source/diff/test evidence, commits/pushes approved changes and creates a Draft PR using narrowly scoped GitHub App identity**. Coder does not receive a GitHub push credential. Artifacts remain in trusted host-managed storage after each container is removed; every handoff is validated by digest, ownership and policy. Test success claimed by Coder alone does not authorize Publish.
- In real code the Controller currently uses Docker CLI/Windows-oriented paths. The above is a **target architecture**; existing Phase 3.1 Docker Desktop runtime, data, tunnel and Workflow Console v2 are preserved until a separately approved transition.

## 3. Eight reconciled architecture decisions

| ID | Owner-approved or proposed baseline | Boundary / condition |
| --- | --- | --- |
| **ADR-01 Deployment** | Modular monolith, one repo, Linux-host MCP/opt-in Controller as separate Compose containers; **Executor outside Compose as systemd host service** | MCP can run alone; Controller dispatch off by default; host Executor activation requires independent gate |
| **ADR-02 Package boundaries** | `workflow_engine` domain/application/ports/SQLite, `mcp_server` auth/transport, `controller` dispatch, trusted `executor` adapter, `coder_worker` stage entrypoints, independent `order_service` | No direct framework/privileged adapter imports into domain; refactor only in atomic reviewed tasks |
| **ADR-03 DRY & contracts** | Minimal pure `worker_contracts` only if shared semantics justify it; one authoritative SHA/path/deadline/error/job rule | No giant utils, DB/OAuth/Podman/secrets in pure contract package; strict DTO compatibility |
| **ADR-04 Persistence** | Python `sqlite3` single-host, one verified **physical Linux-local** SQLite DB shared by MCP+Controller (not Executor/Workers) | Test WAL/locking/UID/backups/concurrency; no unproven Windows bind or network filesystem parity |
| **ADR-05 Migration** | Ordered versioned SQL/Python runner, `PRAGMA user_version` plus exact schema manifest, explicit existing-file checks and operator-controlled backup/restore | No schema creation/migration on ordinary process import/startup; CL-11 owns implementation |
| **ADR-06 Executor** | **B3: dedicated unprivileged Linux-host Trusted Executor using local Rootless Podman CLI**, invoked only via authenticated typed Controller IPC | **Selected for development, NOT production-approved**; no engine socket or arbitrary flag API to Controller; Docker comparator deferred |
| **ADR-07 Workspaces/stages** | Executor owns Linux-host-real workspace ID mapping, immutable source/result handoff, per-stage containers and minimum credentials | Prepare/publish GitHub App only, code model auth only, test none; no .git/PEM/DB/other-project access or world-writable staging |
| **ADR-08 Operations/release** | **GHCR** versioned immutable OCI Worker image digest, pre-pull into Executor rootless Podman store and stage `--pull=never`; fail-closed BLOCKED recovery, deliberate startup and rollback; journald/metrics first, staged **Grafana + Prometheus + Loki + Alloy** | Source SHA ↔ image digest ↔ policy version recorded; no per-stage image pull, automatic retry after ambiguous publish, broad monitoring socket or production deployment without tests |

**Selection status:** ADR-01–08 form a coherent **development design baseline** from accepted subtask ADRs. The Owner must still independently approve this exact consolidated PR/parent CL-12. Runtime implementation and production security sign-off are separate approvals.

## 4. Image release, monitoring, and stage lifecycle decisions

**Worker image/release path:** reviewed source + controlled CI build/test → GHCR (tag for discovery, **sha256 manifest digest for identity**) → explicit release approval → one verified **pre-pull for the Executor's unprivileged Podman identity** → local image store → each ephemeral stage runs from the pinned digest with `--pull=never`. Keep old approved digest for rollback and garbage-collect only scoped unreferenced images. Missing/mismatched/unsupported image must **block** the job. Use scoped registry pull credentials separate from Worker secrets; future CI attestation/SBOM/signature policy requires validation.

**Operations path:** Executor publishes bounded health and metrics (job/stage duration, blocked/unknown state, OOM/timeouts/PID/CPU cgroup events, orphans/cleanup, image cache/digest, ambiguous GitHub publish, systemd restarts). Structured logs go to `journald`, with no PEM/tokens/source or uncontrolled workspace paths. POC can use local metrics + operator alerts. Staging adds **Grafana dashboards, Prometheus, Loki and Alloy/equivalent constrained collector** with TLS/auth/RBAC, redaction, retention, tested alert delivery and no Podman/Docker engine socket to the telemetry stack. Neither monitoring nor registry requires creating four persistent Worker services.

**Lifecycle/handoff:** Executor creates a unique per-stage container with fixed CPU/RAM/PID/timeout/security profile, records the authoritative exit status and stores validated artifact digests in host-owned storage, reaps the stage container, then starts the next stage only after policy success. Stage outputs are **untrusted** until checked, including when produced by a trusted-stage image with compromised inputs. The Publish stage must independently verify approved file changes, base/input SHA, tested artifact digest, branch policy and actual remote state; duplicates or uncertain remote pushes remain **BLOCKED** until reconciliation. Engine container-name uniqueness is **not** durable job idempotency.

**Performance requirement:** Benchmark on the future Executor host: cold pre-pull separately from cached warm start/stop, artifact handoff, and AI/test runtime; record failures and p50/p95 per stage. The discussed goal of stage transitions **under 5% of job duration** is a provisional engineering target, not demonstrated or a reason to weaken isolation.

## 5. Alternatives and non-goals

| Alternative | Decision / reopening condition |
| --- | --- |
| Dedicated rootless Docker daemon (historical B2) | Not selected for current development; reopen only if Podman fails mandatory limits, policy or operability |
| Trusted rootful host Executor (historical B1) | NOT approved or automatic fallback; requires fresh explicit Owner threat/privilege decision |
| Privileged DinD, generic engine socket/proxy to Controller/MCP/AI/monitoring | Prohibited in current threat model |
| Four long-running per-stage Worker services or rebuilding/pulling image per stage | Not required; short-lived distinct containers from GHCR-provisioned local cache |
| Self-hosted registry in current phase | Defer; use GHCR unless reliability/security requirements justify private hosting |
| Full Grafana stack before first isolated POC | Defer to staging; minimum journald/health/alerts required during Executor development |
| Switch from SQLite to Postgres; immediately adopt SQLAlchemy/Alembic | Defer until actual concurrency/HA/migration complexity warrants |
| Django/FastAPI microservice rewrite or broad `common` utility module | Reject without measured value; preserve domain/ports, minimal shared contracts |
| Implicit DB migration, Controller auto-dispatch, automatic retry of uncertain publish | Prohibited; operator gates and fail-closed reconciliation |
| Production deployment from a successful WSL2 probe | Not approved; native Linux host/VM parity and threat model gates mandatory |

## 6. Open implementation and security gates (do not infer PASS)

| Gate | Required evidence / owner |
| --- | --- |
| **G-01 Executor IPC & authority** | Authenticated caller, invalid/replay/conflicting requests, arbitrary flag/image/mount/path rejection with zero container effects, bounded API, socket/transport ownership and denial from untrusted zones |
| **G-02 Workspace/permissions** | Fix/review historical `stage.chmod(0o777)`, worker source mode; effective UID/GID, symlink/hardlink/TOCTOU, secret leakage, sibling-project path escape negatives (CL-02) |
| **G-03 Rootless runtime** | Actual Executor-launched worker seccomp/no-new-privileges/caps/resource cgroups, userns/subuids, timeout, orphan cleanup; private mount propagation impacts; AppArmor/SELinux or documented host compensating policy |
| **G-04 Executor lifecycle** | Durable job-idempotency, crash before/after container create and GitHub push, lost network, cancel/restart, reconciliation of ambiguous publish; no duplicate push |
| **G-05 Stage credentials/network** | Trusted App PEM only prepare/publish; model auth only code; test none/no-network; deny unexpected egress and cross-stage secrets on the actual mounted environment |
| **G-06 SQLite and migration** | Single physical Linux-local file identity, concurrent MCP/Controller WAL/lock/backup/restore, schema manifest + existing-only migrations and rollback without corrupting live Phase 3.1 state |
| **G-07 GHCR release** | Approved digest/provenance/architecture, scoped CI/pull identity, immutable policy, pre-pull + `--pull=never` negative tests, rollback, retention, cold/warm startup benchmark |
| **G-08 Observability** | journald redaction, bounded metrics, health vs dispatch permission, alerts for down/BLOCKED/orphans/OOM/publish unknown; monitoring collector least privilege, TLS/RBAC/retention |
| **G-09 Native Linux parity** | Reproduce actual Executor/Worker isolation, cgroups/user systemd, rootless storage/network, mount ACL and operational behavior on a native Linux VM/host; WSL2 test alone insufficient |
| **G-10 Packaging/regression/review** | Worker-only image packaging (no accidental broad `COPY src`), pure contract/import/wheel resources, CL-07 characterization, real tests/CI, exact-SHA independent review and Owner approvals |

**Podman evidence boundary:** CL-12.5-P2 metadata was reported PASS (Podman 4.9.3/rootless UID 1000 on Ubuntu 24.04 WSL2). CL-12.5-P3 synthetic behavior reported CPU `cpu.max=25000 100000`, 30/31 throttled periods; 64 MiB memory OOM; PID max 32; restricted seccomp, no-network, bounded tmpfs and cleanup. **Tech Lead has not inspected P3's original `REPORT.md`/JSON**; P3 is separately **In Review**. This supports a development engine choice with explicit unresolved risks; not a native Linux or full Executor PASS.

## 7. Parent CL-12 acceptance coverage

| Parent AC | Documentation evidence | Disposition |
| --- | --- | --- |
| AC1 Package direction and security boundaries | CL-12.2 inventory, CL-12.3 boundary matrix, CL-12.5 threat model, CL-12.6 Linux-host layout | Design **documented**, implementation untested |
| AC2 SQLite vs SQLAlchemy/Alembic analysis | CL-12.4 sqlite3 choice and migration alternatives | **Documented**, runner not implemented |
| AC3 Versioned schema/technology decision | ADR-04/05 and CL-12.4 `user_version` + exact manifest design | **Development baseline recorded**, Owner final CL-12 sign-off pending |
| AC4 Backup, deployment and Phase 3.2 rollout gates | CL-12.4 persistence; CL-12.6 startup/backup, GHCR and monitoring roadmap | **Planned**, no live cutover/restore or production proof |
| AC5 Documentation-only CL-12 scope | PRs #8–#12 merged as docs; PR #13 changes only this ADR | No runtime modifications by CL-12 PR #13; must re-check exact diff |

Subtask coverage: CL-12.1 contract, 12.2 inventory, 12.3 boundaries, 12.4 persistence, 12.5 security, 12.6 deployment, 12.7 consolidation. **Completion of this ADR does not by itself approve code migration or Phase 3.2.**

## 8. Proposed atomic implementation DAG after separate Owner authorization

This is a proposal for **future separately scoped tasks** (not a new task authorization, implementation PR or deployed infrastructure):

1. **CL-07 baseline characterization:** Capture current idempotency, code/test, SHA/path/deadline/security, worker GitHub publishing, OAuth and packaging parity in regression fixtures.
2. **CL-09 namespace rename only:** Mechanically rename `my_wfc` → `workflow_engine`; verify imports/wheels/entrypoints; no behavior changes.
3. **Atomic package/contract extraction:** Separate pure job/DTO contracts, persistence ports/DI, MCP/auth, Controller, trusted Executor interface and Worker packaging. Stage behavior and policy authority remain test-covered; each change has path allowlist, exact SHA and individual PR.
4. **CL-11 schema/migration track:** Implement existing-file manifest, ordered runner, constructor DDL removal, verified isolated backup/restore and concurrent Linux-local SQLite tests. No automatic cutover.
5. **Executor development track (separately Owner-approved):** Choose/verify narrow Controller→host IPC transport; implement local Podman worker lifecycle and four-stage artifact handoffs, durable idempotency/reconciliation, safe secrets/network/mounts. Include **GHCR pinned release/pre-pull/cache** and **journald/metrics/alerts** with negative tests; compare native Linux behavior before any production gate.
6. **Staging operational track:** CI image release, restricted monitoring stack as warranted, startup/stop/recovery and alert drills, realistic performance/capacity benchmark, secure release rollback, no host-wide socket/privilege relaxation.
7. **Controlled cutover:** Only Owner-approved CL-10.2/10.3 after persistence/migration tests, target DB physical identity, backup+restore, blocked-job reconciliation, compatible Executor policy and rollback plan. MCP first, Controller opt-in; no implicit worker dispatch.
8. **Remaining cleanup:** CL-01–04/CL-08 where dependencies allow; CL-02 least-privilege fix is a hard prerequisite for live Executor launch. CL-05/06 deferred. Phase 3.2 only after all required cleanup gates.

Every task follows branch/worktree + approved changed paths + tests/negative checks + diff review + commit/push + exact-SHA Tech Lead review (max two rounds) + **Owner merge**; a new SHA invalidates earlier review. Tech Lead-authored architecture drafts require independent Owner/security acceptance, not author self-approval.

## 9. Owner review checklist and non-authorization

- [x] CL-12.5 [PR #11](https://github.com/builacviet/techlead-review-poc/pull/11) merged, conditional B3 Rootless Podman development decision recorded.
- [x] CL-12.6 [PR #12](https://github.com/builacviet/techlead-review-poc/pull/12) merged, Linux deployment + GHCR + monitoring design recorded.
- [x] CL-12.7 [PR #13](https://github.com/builacviet/techlead-review-poc/pull/13) Owner-reviewed/merged at exact head `57965bef2de386cf42096a12a9f2d629cb4949b3` as `5cae1207bc40f9c75185017ae9f7f630e9f9feb6`.
- [ ] Explicit **parent CL-12 final architecture sign-off**, if required by the project gate, is **not inferred from individual PR merges**.
- [ ] P3 original REPORT/JSON independently inspected if P3 evidence is to be formally accepted; otherwise preserve clearly as reported, not verified.
- [ ] New atomic implementation tasks, exact source/config allowlist and Owner approval granted separately before any Executor code, GHCR CI or Grafana installation.
- [ ] Before production, native Linux parity, security negative tests, DB backup/restore, alert and crash-reconciliation evidence satisfy the gates above.
- [ ] Phase 3.1 data, Docker Desktop, GitHub App credentials, separate Workflow Console v2 and external side effects preserved until authorized change.

**Authoring/review provenance:** This document is directly authored by ChatGPT Tech Lead from merged repository ADRs, Notion records and Owner decisions. No new `pytest`, local `git diff --check`, CI run, Linux/WSL security experiment, Docker/Podman Worker execution, DB or runtime action occurred while writing this ADR. GitHub exact-head verification and file allowlist verification are required after commit; the document intentionally does not embed its own HEAD SHA.

**Post-merge stop gate:** PR #13 was Owner-merged as a documentation-only architecture decision. This does **not** activate the Executor, change the running Controller, waive native Linux/P3 evidence gates, or authorize any broad Phase 3.2/production task. Each implementation step requires a separately approved atomic scope and security acceptance. 
