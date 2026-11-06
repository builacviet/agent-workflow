# CL-12.7 — Consolidated architecture decision record and handoff

**Status: Tech Lead-authored DRAFT for Owner decision, NOT final ADR approval.** Date: 2026-11-06. Task: https://app.notion.com/p/410f92014abd810284d7e98107016b61

This document consolidates CL-12.1–CL-12.6 at the authorized known main base 937d0ba2203bb4cff0a63ea5a6ac3bc094b5137e. The Owner requested simultaneous *drafting* of CL-12.5, CL-12.6 and CL-12.7 by Tech Lead. **It does not waive dependency order for review/merges, runtime/security validation, final ADR sign-off or implementation authorization.** Treat the draft CL-12.5/12.6 decisions below as provisional until their PRs are approved and merged. This PR changes documentation only; no source/Compose/DB/runtime changes.

## 1. Context and linked evidence

| Subtask | Purpose/evidence | Status at drafting |
| --- | --- | --- |
| CL-12.1 | [Task and review contract](../tasks/CL-12.md) | Previously Owner Approved and merged |
| CL-12.2 | [Source inventory: 24 Python modules, import graph and trust boundaries](CL-12-current-state.md) | Approved; GitHub PR #8 merged |
| CL-12.3 | [Target modules, DI and DRY ownership matrix](CL-12-module-boundaries.md) | Approved; GitHub PR #9 merged |
| CL-12.4 | [Single-host SQLite, migrations, backup and recovery](CL-12-persistence.md) | Approved; GitHub PR #10 merged |
| CL-12.5 | [Executor threat model](https://github.com/builacviet/techlead-review-poc/pull/11) / proposed CL-12-executor-security.md | Tech Lead draft PR #11, awaiting Owner and independent security review |
| CL-12.6 | [Deployment/workspace/operations](https://github.com/builacviet/techlead-review-poc/pull/12) / proposed CL-12-deployment.md | Tech Lead draft PR #12, dependent on CL-12.5 approval |
| CL-12.7 | This consolidated ADR | Draft PR; acceptance depends on both preceding drafts |

Controlling decisions: [Parent CL-12 Notion ADR Decision Set v0.2](https://app.notion.com/p/410f92014abd81cdbceded689b7d4945), [CL-10.1 DB audit](https://app.notion.com/p/410f92014abd8186b104d65641e0c9ca), repository AGENTS.md, docs/architecture.md, docs/review-policy.md and docs/handoff.md. The historical status notes on the parent include superseded Proposed drafts; the specific later Owner authorizations and actual PR merges govern each completed subtask.

Evidence classifications: **Observed** = static source/config at recorded SHA or dated CL-10.1 operator-reported audit; **Decided baseline** = Owner-preferred design pending tests/sign-off; **Validation gate** = test still to perform; **Proposal** = not authorized to implement. A passing existing pytest suite does not verify a newly proposed architecture.

## 2. Proposed deployment and trust flow

~~~text
ChatGPT Tech Lead
   | OAuth-secured workflow review (not Docker control)
TLS ingress -> [MCP interface] ----+
                                   | same *physical* Linux-local
                              [workflow_state SQLite]
                                   |
                         [Controller] (opt-in; off by default)
                                   | authenticated bounded typed job
                            [Trusted Executor]  <- only after CL-12.5 gate
                                   |
                         [Dedicated rootless worker daemon] (candidate B2)
                                   |
                       +-----------+-----------+
                 trusted prepare  code        test       trusted publish
                 Git/App scoped  model auth   none       Git/App scoped
                        Git metadata stays in trusted stages
~~~

Keep code/test untrusted, with source snapshot and strict write-path policy; model code has only needed model egress, test is no-network and credential-free. Prevent socket, PEM, .git, SQLite, secret env and other project paths from entering code/test. Trusted publish validates exact remote SHA/ancestry before ordinary push/PR handoff; Tech Lead independently verifies GitHub and Owner approves merge.

**No Docker socket in MCP/Controller/Codex.** Executor's privileged capability must be explicit and narrowly authorized. A generic socket proxy is insufficient. Rootless dedicated daemon is a preference, not an already proven secure deployment; privileged DinD is prohibited. B1 minimal trusted host executor requires a separately signed Owner decision if B2 tests fail. Current implementation still uses local Controller Docker CLI; this diagram is not proof of current runtime.

## 3. Eight decision records (CL-12 Decision Set v0.2)

| ID | Preferred baseline | Rationale and hard stop |
| --- | --- | --- |
| ADR-01 Deployment | Modular monolith within one repo; separate MCP and Controller services in Linux Compose; Windows Docker Desktop for development | No microservice proliferation, Controller not implicitly started by MCP |
| ADR-02 Boundaries | workflow_engine domain/application/ports/SQLite, mcp_server adapters, controller orchestration, worker_executor boundary, coder_worker stages, independent order_service | No framework rewrite or cross-import of privileged adapters into core; runtime behavior preserved |
| ADR-03 DRY | Conditional pure worker_contracts for only genuinely shared SHA/path/deadline/error/job values, with single policy owner and no cycles | No broad utils or IO, DB, Docker, OAuth, secrets in shared package; Pydantic strictness preserved across any DTO move |
| ADR-04 DB | sqlite3 single host, one physically shared Docker-managed Linux-local volume; MCP/Controller as separate trusted writers | No network filesystem/Windows bind as assumed safe; Postgres only after measured concurrency/HA/multi-host need |
| ADR-05 Schema | Ordered SQL/Python runner, PRAGMA user_version plus exact schema manifest, existing-only location checks, one-shot pre-start migration, verified backups | CL-11 owns implementation/constructor DDL removal; no implicit bootstrapping/DDL or unreviewed Alembic adoption |
| ADR-06 Executor | Authenticated strict typed internal job API, conditionally dedicated rootless worker daemon (B2); Controller and MCP lack daemon authority | B2 platform/negative tests required; no privileged DinD or automatic fallback; B1 host executor needs new Owner sign-off |
| ADR-07 Workspaces | Executor owns daemon-host-real mapping for logical task workspace IDs; stage snapshot isolation and minimum secret scope | No Windows absolute-path contract in target, arbitrary host mount, .git/PEM/DB leaks, or world-writable stage by default |
| ADR-08 Operations | Independent pinned images/entrypoints, explicit start/health/schema gate, fail-closed BLOCKED reconciliation, isolated restore and rollback | No deployment-triggered dispatch, credential in image, hot SQLite raw-copy, unbounded retry or volume deletion |

The selected baseline is **not** a sign-off for rootless engine implementation or production persistence cutover. Technology selection remains subject to final Owner approval plus empirical gates.

## 4. Alternatives explicitly rejected or conditional

| Alternative | Disposition | Revisit when |
| --- | --- | --- |
| Keep Windows host Controller + Linux-container DB bind as production architecture | Reject as unvalidated cross-OS SQLite locking/path contract | Only if separately proven and explicitly approved; Linux-local single-host target preferred |
| Adopt Postgres immediately | Defer | Measured lock contention/SLO, multi-host writers or HA requirements justify cost |
| Switch to SQLAlchemy/Alembic immediately | Defer | Multiple backends, repeated complex SQLite rebuilds or maintenance metrics justify adapter/migration overhead |
| Rewrite as Django/FastAPI/microservices | Reject for present scope | Concrete requirements show existing FastMCP/service adapters insufficient |
| Direct /var/run/docker.sock in Controller/MCP; generic Docker proxy | Reject by default | No implicit relaxation; explicit new security/Owner decision required |
| Privileged Docker-in-Docker for rootless worker engine | Reject | Not an acceptable B2 implementation under current risk baseline |
| Trusted host executor B1 | Conditional fallback | B2 fails documented capability/security tests; Owner reviews and approves narrowly scoped IPC/privileges |
| Share a giant worker/common utils module | Reject | Only minimal pure contracts with real matching consumers and negative import tests |
| Automatic migrations, auto-redispatch or rollback to stale DB | Reject | Operator-approved one-shot migration, reconcile uncertain Git/worker and later-write effects first |

## 5. Known security and engineering blockers

| ID | Why blocked | Required future gate/owner |
| --- | --- | --- |
| G-01 Executor privilege | Rootless engine deployment, seccomp/AppArmor/UID/pids/resources, engine socket separation not tested on Linux/Desktop | CL-12.5 negative test plan and separately approved Executor implementation; Owner reviews B1 fallback |
| G-02 Permissions | coder_runtime.py:324–325 uses stage.chmod(0o777); worker source also broadens permissions. Effective ACL/UID attack path untested | CL-02 and CL-12.5 exact ownership/least-privilege, path-race and secret-isolation tests |
| G-03 DB concurrency | Present live database uses Windows bind; same-host Linux-local WAL/busy_timeout/BEGIN IMMEDIATE not proven with two processes | CL-11 plus CL-12.6 target filesystem tests |
| G-04 Migration/recovery | v1 schema has both dispatch-table-present and absent possible variants; constructor DDL, no existing-only manifest gate | CL-11.1–11.3 runner/fingerprint/migrate/restore tests, no live change without cutover approval |
| G-05 Workspace/cross-platform | Absolute linked worktree and daemon-host path mapping/UID/link behavior may diverge on Docker Desktop and Linux | CL-12.6 parity/negative tests; no arbitrary mount controls |
| G-06 Packaging/dependencies | Worker image COPY src; potential accidental workflow dependency and resource lookup drift after extraction | Separate package/image changes with wheel tests, forbidden imports and secret scan |
| G-07 Behavioral parity | Shared DTO, error, deadlines, SHA and path policy require exact acceptance/cancellation/timeout behavior | CL-07 characterization before CL-09 rename and independent extraction |
| G-08 Operational recovery | Crash after Git push or worker publish can leave unknown effects; restoring SQLite may lose later records | Fail-closed BLOCKED reconciliation, isolated backup restore, Owner cutover gates |
| G-09 Review integrity | Tech Lead authored CL-12.5/6/7, so cannot present them as independent passed review or Owner approval | Independent Owner/qualified security review; exact SHA plus one-by-one merge |
| G-10 Documentation dependency | CL-12.5/12.6 drafts may change before approval, invalidating this synthesis | Reconcile against merged exact SHA before final CL-12.7 acceptance |

No claim is made that these gates passed. The extant 179 passed/1 skipped Coder test reports for prior documentation PRs are historical regressions, not security/platform validation of this target.

## 6. Parent CL-12 AC reconciliation and task coverage

| Parent AC | Evidence | Current result |
| --- | --- | --- |
| AC1 Modules/dependency direction and trust separation | CL-12.2 observed inventory; CL-12.3 target import/DI; CL-12.5/12.6 proposed trust/deployment diagrams | **Documented**; implementation/negative tests pending |
| AC2 SQLite migrations versus SQLAlchemy/Alembic comparison | CL-12.4 ADR cost, transaction limits, fixture/restore matrix | **Documented**, future runner unimplemented |
| AC3 Technology/schema versioning decision after Owner review | ADR-04/05 baseline and CL-12.4 user_version+manifest proposal | **Preferred baseline**, final Owner sign-off pending |
| AC4 Backup/snapshot, deployment and Phase 3.2 gate | CL-12.4 recovery; CL-12.6 deployment/runbook; CL-10 inventory | **Planned** only; no live backup/cutover exercise |
| AC5 No runtime/code change during CL-12 ADR | CL-12.1–12.4 were docs-only PRs; CL-12.5/6/7 are proposed docs-only | **Document-only scope maintained in submitted diffs**, must recheck each PR before merge |

Seven subtasks coverage check: 12.1 contract; 12.2 inventory; 12.3 package/DRY; 12.4 SQLite; 12.5 Executor; 12.6 deployment; 12.7 synthesis. **Do not mark all seven Approved** until individual PRs pass their acceptance and Owner merge, or claim the parent ADR is final from document creation alone.

## 7. Atomic implementation DAG and no-go conditions

This is a **proposed task graph**, not automatically created Notion records or authorization to write source/DB:

1. **Baseline:** CL-07 captures current reviews, attempts, immutable history, dispatch idempotency, code/test sandbox, OAuth, path/SHA/DTO validation, deadline, failure and packaging behavior. Preserve all regression fixtures and exact version evidence.
2. **Namespace only:** CL-09 mechanically renames my_wfc -> workflow_engine (flat initially) with import/resource/entrypoint parity. Do not slip in policy/SQLite/Executor behavior changes.
3. **Package extraction as separate Owner-authorized tasks:** pure SHA/error contracts; lexical paths; deadline math; repository ports and DI; domain/DTO compatibility; MCP auth/tool extraction; Controller split; worker contract/packaging. Each slice gets its own tests, rollback boundary, exact allowlist and PR; the sequence may be refined after characterization. Do not assume moving files creates isolation.
4. **Schema track:** CL-11.1–11.3 implements version-manifest, controlled migrations, constructor DDL removal and isolated backup/restore tests. Can be planned in parallel, but deployment of new DB writer processes requires a verified compatible schema and final prior gates.
5. **Executor track:** only after CL-12.5/12.6 security/platform approval, implement typed Executor and dedicated daemon isolation in distinct atomic tasks. If B2 fails, require Owner B1 decision; no privileged fallback. CL-02 permission fixes must pass before Executor production use even if the cleanup backlog scheduled CL-01..04 later.
6. **Operational cutover:** CL-10.2/10.3 only after final ADR, reviewed CL-11, target volume/lock tests, backup + isolated restore, release compatibility and Executor/Controller enablement decision. Start MCP alone and verify OAuth/DB; Controller remains opt-in, not automatically dispatched.
7. **Remaining cleanup:** CL-01..04 (security/performance/permission hardening) and CL-08 docs/runbooks with dependencies on code extracted. Any safety-critical CL-02 fix needed for Executor launch is a **precondition**, regardless of nominal backlog ordering. CL-05 and CL-06 stay deferred. Phase 3.2 must wait for all required cleanup gates; no implicit activation.

Per-task gate: isolated branch/worktree, exact base SHA and changed-path allowlist, review of diff, actual pytest/negative integration evidence (or blocker), ordinary push, PR, exact-head Tech Lead review at most 2 rounds, Owner approval before merge, next-task authorization separately. A new SHA invalidates prior review. Tech Lead-authored documentation requires an independent acceptance check; author does not self-sign an impartial review.

## 8. Final Owner decision checklist (not yet satisfied)

- [ ] PR #11 CL-12.5 independently reviewed for capability/threat details and accepted/merged.
- [ ] PR #12 CL-12.6 reconciled against merged CL-12.5 and accepted/merged.
- [ ] This PR rebased/verified against the two accepted documents, all dependency links resolved, exact SHA reviewed; accepted/merged.
- [ ] Owner signs full parent CL-12 ADR-01..08 baseline and notes unresolved security/portability gates without authorizing implementation.
- [ ] Owner authorizes only the next specific atomic code task, never generic Phase 3.2 or production migration.
- [ ] Rollback, secrets, storage/legacy preservation and external effects are included in future implementation/runbook review.

## 9. Review handoff / evidence limitations

**Author:** ChatGPT Tech Lead acting on Owner request for documentation. **Base at authoring:** 937d0ba2203bb4cff0a63ea5a6ac3bc094b5137e. **Allowlisted change:** docs/adr/CL-12-architecture.md only. Branch/head SHA, remote state and PR URL must be recorded in PR metadata after commit; a file cannot contain its own commit SHA.

Source verification comprised read-only GitHub files/Notion task specs and public Docker/SQLite documentation. No pytest, Docker, Compose, static AST job, Linux security test, live DB probe, migrations, backup, Controller dispatch or deployment was executed by Tech Lead. Any regression/security test numbers elsewhere are historical and not reproducible evidence for these new drafts.

**Stop:** All three Tech Lead-authored ADR PRs await independent review and sequential Owner merge. No self-APPROVE, auto-merge, next task creation, live security experiment or refactor from this document.