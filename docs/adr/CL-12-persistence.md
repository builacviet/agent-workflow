# CL-12.4 — SQLite persistence, schema migrations and recovery

## Status, authority and scope

Documentation-only ADR proposal, 2026-11-06, for
`builacviet/techlead-review-poc`. Actual base and freshly fetched `origin/main`:
`e108a42bc4e2514ad837bd808003f7b3d49673ca`.
Branch/worktree: `codex/cl-12.4` at
`D:/WORK/Project/techlead-review-poc-CL-12.4`.

Sources and governing handoff:

- [Authorized CL-12.4 task](https://app.notion.com/p/410f92014abd8100ab84da07b4057f75), including its later Owner authorization.
- [CL-10.1 approved audit](https://app.notion.com/p/410f92014abd8186b104d65641e0c9ca): operator-reported inventory and authority selection, not a new inspection by this Coder.
- [Parent CL-12 Decision Set v0.2](https://app.notion.com/p/410f92014abd81cdbceded689b7d4945), particularly ADR-04/05/08.
- [CL-12.3 PR #9 and full Coder handoff](https://github.com/builacviet/agent-workflow/pull/9), merged at this base; `docs/adr/CL-12-module-boundaries.md` and `docs/adr/CL-12-current-state.md`.
- `AGENTS.md`, `docs/architecture.md`, `docs/review-policy.md`, `docs/handoff.md`, `docs/tasks/CL-12.md`; relevant source/schema/config and existing tests.

Only `docs/adr/CL-12-persistence.md` changes. Historical Proposed/locked notices
are superseded by the explicit CL-12.4 authorization, not by a general release
of future work. This ADR does not implement migrations, approve the final parent
ADR or authorize CL-12.5. No real DB access/probe/copy, service restart, Docker
experiment, configuration/volume/credential change or Controller dispatch occurs.
Future procedures below are plans requiring separate authorization, not commands
executed in this task. File/line references are one-based at the base above.

## AC1 — Inherited audit provenance and current state

The following is **CL-10.1 operator-provided read-only evidence from 2026-11-06**.
It is not fresh host/DB verification by Codex and may change after that audit.
The task authorization and parent record Owner's selection of the running
Phase 3.1 MCP DB as authoritative. Earlier audit prose saying selection was
pending is historical; no new authority decision is made here.

| Audit field | Phase 3.1 authoritative DB | Stopped legacy POC DB |
| --- | --- | --- |
| Container/path reported | my-wfc-phase31-mcp-1; /state/workflow.sqlite3 | techlead-review-poc-mcp-1; /state/workflow-poc-unauth.sqlite3 |
| Storage reported | Windows bind D:/WORK/Project/techlead-review-poc-oauth/data/controller -> /state | Named volume techlead-review-poc_mcp_state -> /state |
| Host DB path | D:/WORK/Project/techlead-review-poc-oauth/data/controller/workflow.sqlite3 | Docker-managed volume; no Windows file path inferred |
| Version / quick_check | user_version=1 / ok | user_version=1 / ok |
| File size | 53,248 bytes | 53,248 bytes |
| tasks / review_attempts / reviews / review_findings / task_events | 0 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 0 |
| coder_dispatches | TABLE MISSING | Not present |
| Operational role | Running MCP's current POC state, selected by Owner | Stopped deployment; preserve as historical archive |

Equal sizes/versions/counts do not establish byte equality. Zero rows means no
workflow-row merge is indicated by that inventory, not that backup or provenance
may be skipped. Other legacy volume contents were not fully enumerated. Preserve
`techlead-review-poc_mcp_state` unmodified, do not restart the old deployment,
merge its data, archive/delete it or decommission it without separate approval.

Missing `coder_dispatches` is consistent with DispatchLedger not having been
initialized; it is not by itself corruption. The audit found no Controller
process/config in checked Windows locations, without proving it never ran or
that every possible configuration was searched. The connector's empty queue
alone did not identify its DB; operator mount/env inspection supplied that link.
No concurrency or WAL-safety conclusion follows from a successful quick_check.

### Observed code/config versus proposed changes

| Current evidence | Observed behavior and implication |
| --- | --- |
| src/my_wfc/paths.py:6–17 | Explicit WFC_DB_PATH must be absolute; fallback differs between checkout and installed-user data. Absolute path validity does not prove intended DB identity. |
| src/my_wfc/tools.py:10–17; service.py:19–41 | Authenticated tools construct default WorkflowService(database_path()); repository injection exists, but default construction wires SQLite. |
| src/my_wfc/repository.py:185–196 | Constructor creates parent directory, accepts user_version 0/1 and executes schema.sql. It can silently create a new file through sqlite3.connect; this is not the target startup gate. |
| src/my_wfc/schema.sql:1–38 | Five workflow tables, active-claim unique index, exact review-round constraints and user_version=1; no coder_dispatches DDL in this resource. |
| src/my_wfc/repository.py:199–235 | sqlite3 connections use isolation_level=None, timeout capped by operation_timeout(30), foreign_keys=ON; writes explicitly BEGIN IMMEDIATE, check deadline, update busy_timeout then commit, rollback on exception; reads BEGIN. No journal_mode=WAL setting is present here. |
| src/my_wfc/dispatch_repository.py:13–27,29–100 | Constructor runs CREATE TABLE IF NOT EXISTS coder_dispatches independently of user_version. Dispatch operations use raw transactions, unique active slot and task/round/input-SHA identity. A v1 label alone does not distinguish presence/shape of this table. |
| compose.controller.yaml:1–34; src/my_wfc/controller.py:104–109 | Compose declares MCP with host data bind and explicit DB path, not a Controller service. Native Controller uses config database field. Matching configuration must refer to the same physical file, not just equal-looking path strings. |
| src/my_wfc/service.py:115–180,287–442,456–457 | Local authorization/SHA/review/immutable-history rules remain application-owned; remote approval freshness stays unknown. Migration does not add remote verification or review powers. |

The current Windows-host/Linux-container bind is a portability/locking risk
requiring environment-specific validation, not evidence of existing corruption.
This ADR does not claim concurrent native Windows Controller and Linux MCP SQLite
access is safe, nor change the running mount to the target design.

## AC2 — Decision and tradeoff

**Preferred ADR-04/05 baseline:** keep stdlib sqlite3 on one host and introduce a
small ordered SQL/Python migration runner in CL-11. This is not implemented.
Keep current WorkflowRepository/WorkflowUnit boundaries; preserve review rules,
SHA equality, history, claims, dispatch idempotency and failure reconciliation.
No scheduler, new evidence table, ORM or dependency is added by this document.

| Criterion | sqlite3 + ordered SQL/Python runner | SQLAlchemy 2 + Alembic |
| --- | --- | --- |
| Change/code cost | Reuse existing explicit SQL, units and transaction boundary; small runner but team owns version/manifest checks and failure tests | Introduces engine/metadata/revision tooling and dependencies; using SQLAlchemy Core need not imply an ORM, but adapter and migration integration still need work |
| Ordering/version | Reviewed linear manifest, explicit from/to versions and deterministic validation; user_version is application metadata | Revision history/dependency tooling; must still establish an unambiguous baseline for current v1 variants and coordinate startup |
| DDL/transactions | Explicit supported statements and transaction control; driver/version behavior must be tested | Tooling does not remove SQLite limitations; batch table reconstruction can add copy/constraint/index preservation concerns |
| Testability | Direct disposable fixtures exercise existing adapter and runner; failure injection/manifest checks are our responsibility | Rich revision tooling and metadata comparison; generated changes still require review and populated-fixture tests |
| Recovery/rollback | Transaction rollback for supported steps; verified snapshot restore for destructive/uncertain outcomes | Downgrade scripts can exist but cannot inherently recover discarded data or undo GitHub/worker effects; backups remain necessary |
| Complexity/portability | Lowest incremental complexity for current single-host explicit-SQL POC | Worth revisiting if multiple supported DB backends or sustained schema evolution justify migration/framework overhead |

SQLite may require table reconstruction for some schema alterations; Alembic
supports a batch move/copy path. Neither approach makes data-loss rollback
automatic. See [Alembic batch migration documentation](https://alembic.sqlalchemy.org/en/latest/batch.html).
This comparison is a design judgment for this repository, not benchmark evidence.

Reconsider PostgreSQL when measured lock-wait/SQLITE_BUSY rates or write latency
breach an Owner-agreed SLO under representative load despite bounded short
transactions, or when multi-host writers/HA become required. Reconsider
SQLAlchemy/Alembic separately when repeated custom table-rebuild migrations,
multiple backends or migration-maintenance effort demonstrably exceed tooling
cost. Future reports must record workload, concurrent writers, p95/p99 latency,
busy/error rate, restore time/data-loss window and maintenance effort; thresholds
are not invented here or claimed already measured. Choosing ORM tooling and
choosing PostgreSQL are separate decisions needing new review/authorization.

## AC3 — Proposed version, migration and concurrency contract

### Ownership and explicit startup

CL-11 owns runner implementation, schema manifest/version choices, deterministic
preflight and removal of implicit DDL from both repository initialization and
DispatchLedger construction (particularly CL-11.2). CL-12 defines requirements
only. CL-12.3 assigns SQLite adapters/migrations under
workflow_engine.infrastructure.sqlite, with roots injecting repositories/storage;
namespace rename/package extraction must not alter schema or run migration.

One explicit operator migration action runs before MCP/Controller startup;
services never migrate on import/construction or on an authenticated read tool.
Both services independently validate expected schema and intended DB location,
then refuse to start on mismatch. Deploying MCP must not start Controller or
release a blocked dispatch. Shared pure workflow code and DB do not gain Docker
socket, network adapter, credentials or secret-loading responsibilities.

### Preflight and version gate (future runner)

1. Require an operator-approved absolute canonical DB path, expected storage identity/role, target version and release/migration-manifest digest. Resolve only approved locations; reject missing mount/file, symlink/alias surprises, wrong project/legacy path, unsupported ownership or unwritable intended storage. Never fall back to another checkout/home DB for migration or production startup.
2. Require an existing regular intended DB; open existing-only (SQLite URI mode=rw for authorized writes, mode=ro for inspection) rather than connect's create-if-missing default. A separate explicitly authorized fresh-bootstrap operation must verify an empty target and initialize it; missing DB/version 0 is not permission to silently bootstrap. Existence checks alone are insufficient against path changes; validate the opened identity under controlled ownership.
3. Disable intake/dispatch and quiesce both DB writers plus admin mutations; confirm no second runner and no active external worker effects needing reconciliation. A per-DB migration lock and controlled process lifecycle prevent competing runners/startups; BEGIN IMMEDIATE alone is not a service lifecycle lock. Acquire exclusive migration ownership before backup/version checks; recheck identity/version after obtaining the transaction lock.
4. Read user_version, schema objects/columns/indexes/constraints, quick_check/integrity_check and foreign_key_check as appropriate. Compare to an exact reviewed source schema manifest, not only an integer. Unknown, newer, malformed or partly-applied shape fails closed without rewriting user_version. Require a completed, verified pre-change backup and sufficient disk space/permissions.
5. Handle audited v1 variants explicitly: five-table v1 with coder_dispatches absent and known v1 with the constructor-created table may both occur. CL-11 must define fingerprints and fixtures for each supported form; verify an existing dispatch table's complete shape/data/invariants, never hide drift behind IF NOT EXISTS. Whether adding formal dispatch schema increments to a particular version is a CL-11 reviewed manifest decision, not a version silently assigned in this ADR.
6. Use a packaged immutable linear from_version -> to_version manifest; no gaps, duplicate IDs, branch ambiguity, unknown code or out-of-order execution. Record migration identity/digest, source/target versions, release SHA, time, backup reference and outcome in external operator evidence; no new evidence table is authorized here. user_version is the application-owned version marker, not proof of schema correctness by itself. See [SQLite PRAGMA documentation](https://sqlite.org/pragma.html#pragma_user_version).
7. Execute supported migration steps under explicit transaction control; update user_version in the same successful schema/data transaction, then commit. Prefer one transaction for the authorized pending batch where supported. Recheck schema/data/version after commit, reopening an isolated verification connection. Services require the target manifest to match before startup. A no-op rerun validates exact target shape and makes no writes.

Transactional DDL is not a blanket guarantee for arbitrary scripts. Prohibit
unreviewed BEGIN/COMMIT in steps; operations such as VACUUM or journal-mode changes
need separately planned maintenance outside the schema transaction. Validate
foreign-key/table-rebuild semantics and indexes/triggers with populated fixtures.
Python sqlite3 transaction/autocommit behavior and executescript can commit a
pending transaction in legacy transaction mode; do not assume wrapping today's
executescript call makes a whole migration atomic. Select and test explicit
statement/transaction execution for supported Python versions before CL-11
implementation. See [Python sqlite3 transaction control](https://docs.python.org/3.12/library/sqlite3.html#transaction-control).

Failure before commit must roll back supported transactional work and preserve
source version/data. After any crash, lost connection or unknown commit outcome,
keep both services stopped and inspect version plus manifest/data from a safe
operator context. Do not auto-retry, decrement version or mark success. If the
state cannot be established, quarantine it and restore a verified backup only
after controlled shutdown and explicit operator decision. Partial committed
multi-step maintenance needs a reviewed recovery plan, not a claim of atomicity.

### Single-host concurrency and journal policy

Target: MCP and Controller are separate Linux processes/containers mounting the
same physical Linux-local Docker-managed persistent volume on one host, with
consistent UID/GID/directory permissions and verified file identity. No shared
network filesystem, separate DB copies, per-container private state or concurrent
native Windows writer is assumed safe. Only these trusted adapters/admin tooling
write workflow state; Executor/Worker/code/test never mount it.

WAL is a **candidate target after validation**, not a statement of current mode.
WAL needs same-host shared memory; it still permits only one writer, needs
checkpoint/sidecar management and can return busy errors. Long read snapshots
can delay checkpoints. Retain a tested rollback-journal configuration if WAL is
not validated; do not silently switch mode or assume that rollback journaling
solves Docker Desktop cross-OS semantics. Gate both journal modes on the actual
supported storage/runtime. See [SQLite WAL documentation](https://sqlite.org/wal.html).

Retain BEGIN IMMEDIATE for short mutation units to acquire write intent before
reading decision state; handle SQLITE_BUSY on acquisition/commit explicitly,
with rollback and bounded errors. Configure connection-local foreign_keys and
busy_timeout before lock acquisition, capped to remaining dispatch/operation
budget, and re-evaluate before commit. Current source sets the explicit PRAGMA
late but sqlite3.connect already has a timeout; do not claim it has no lock wait.
No Git/Docker/HTTP or long model work belongs in a DB transaction. No unbounded
retry or retry of uncertain external effects. Read-only units remain short.
SQLite's single-writer behavior and BEGIN IMMEDIATE busy handling are described
in [SQLite transaction documentation](https://sqlite.org/lang_transaction.html).

Future two-process tests must cover parallel MCP/Controller reads and writes,
claim/dispatch contention, deadlines, rollback, abrupt termination/reopen,
checkpoint behavior, disk-full/read-only failures and measured busy/latency rates
on the same target volume. The existing concurrency tests on disposable local
fixtures do not prove Windows bind sharing, WAL or deployed-container parity.

## AC4 — Backup, isolated restore, rollback and cutover gates

All procedures below require a separately authorized operator task. None is run
against a real database in CL-12.4. Keep private state/backups outside Git and
code/test mounts, with restricted ownership and Owner-approved retention.

### Backup and restore rehearsal

1. Identify authoritative source by approved physical path/mount/release and version manifest. Select a unique backup destination outside the active DB path/volume failure domain; ensure capacity and access, reject overwriting the source/only known-good backup. Record identity, source version, application release and intended recovery objective without secrets.
2. Prefer SQLite online backup API to create a consistent standalone snapshot from the source's supported local runtime. Bound waits and verify completion; a failed/partial destination is not a usable backup. Online backup may coexist with writers for routine snapshots, but migration/cutover requires intake stopped and both writers/admin quiesced so the final snapshot/data reconciliation has a fixed boundary. Do not run a native Windows backup connection concurrently against the Linux-shared live DB by assumption. See [SQLite backup API](https://sqlite.org/backup.html) and [Python Connection.backup](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup).
3. Alternative: coordinated shutdown of all writers/readers with confirmed clean close and journal/WAL recovery/checkpoint handling under the approved journal policy, then snapshot the fully quiescent consistent database. Never raw-copy an active WAL database or blindly discard -wal/-shm/-journal files. Prefer the SQLite backup path even during downtime; filesystem copy is acceptable only after a tested clean-close procedure proves no committed data remains solely in sidecars.
4. Verify the completed backup's version/schema, integrity_check and foreign_key_check, table counts and relevant data invariants. Record size/digest of the completed closed artifact as provenance, not a replacement for logical checks. Use a stable quiesced source when comparing row sets; a changing source may legitimately differ from an online snapshot.
5. Restore into an isolated disposable destination with no production mounts, credentials, tunnel or dispatch ability. Run schema/data checks and local service/repository regressions; exercise the intended migrations and recovery on this clone only. Record actual restore duration, recovered snapshot boundary, release compatibility and all failures. A successfully created backup without a successful restore rehearsal does not pass the gate.

### Pre/post migration reconciliation and rollback

Before migration/cutover, record version/schema fingerprint, all five workflow
table counts and full logical content/invariants appropriate to task metadata,
reviews/findings/events/claims; include dispatch records/slot state if present.
Audit-time zero rows are not a substitute for this future preflight. Preserve
exact task/repo/branch/base/head references, immutable history and review counts.
Do not reset approvals, leases, blocked dispatches or idempotency identities.
Post-migration compare expected unchanged data and explicitly documented
transformations/new objects; verify version, indexes/constraints, integrity and
foreign keys. Do not mark a missing dispatch table as corrupt before applying
the reviewed supported-v1 rules.

On failure, stop intake and ensure no process holds/reopens the failed DB;
confirm external workers/effects and retain blocked reconciliation. Transaction
rollback is first-line for known uncommitted supported steps. For committed,
destructive or uncertain failures, retain the failed artifact for diagnosis and
restore the verified pre-change backup into a separate controlled path after
shutdown; validate old schema/data and use the matching prior application release.
Switch only under operator approval. Never overwrite files held by a running
SQLite process. Restoring a backup can discard later accepted writes, and cannot
undo Git pushes/PRs or running workers: if writes/effects happened after the
snapshot, require explicit reconciliation/data-loss decision rather than
claiming transparent rollback. No automatic downgrade or redispatch.

### Future CL-10.2/10.3 operational move

| Gate | Required future evidence/decision |
| --- | --- |
| Authorization | Final CL-12 ADR sign-off and separately approved CL-11 implementation plus CL-10.2/10.3 exact task/allowlist/maintenance plan; Owner approves live impact |
| Storage readiness | Named Linux-local persistent target volume, correct permissions/capacity, both service paths resolve to the same physical DB; no secret/socket access in core; Linux/Desktop journal/locking checks completed |
| Freeze/reconcile | Disable new workflow mutations/dispatch, stop/quiesce writers/admin and reconcile outstanding workers; establish final snapshot boundary, verified backup and tested rollback |
| Copy/restore into target | Use the verified SQLite snapshot with target DB opened only by authorized operator process; verify version/schema/content/integrity before migration and after any approved migration. Source remains preserved |
| Release compatibility | Pin application/migration versions, validate schema gates and per-service configuration; no simultaneous old/new writable deployments and no legacy DB merge |
| Controlled activation | Owner-authorized MCP activation/health/auth/read checks first; Controller remains explicitly disabled until separately authorized and safe dispatch state confirmed. Never deploy MCP with implicit dispatch startup |
| Acceptance/rollback window | Compare data and errors to frozen baseline, record target identity/restore evidence and outcome; keep old source inaccessible to writers but preserved through agreed window. Any unknown state blocks activation |
| Retirement | Separate Owner approval for legacy archive/decommission or source deletion; never docker compose down -v to remove persistent state |

Empty workflow tables reduce merge complexity, not backup or rollback duties.
No route, mount, volume, service lifecycle or authoritative-source switch is
performed by writing this ADR. CL-12.5/12.6 own later security/portability details.

## Future implementation validation matrix and handoff limits

| Gate / later owner | Required deterministic or controlled evidence |
| --- | --- |
| CL-11 preflight/version | Missing/wrong DB path never created; v0 bootstrap separate; unknown/newer/drifted schemas fail closed; both supported v1 dispatch variants; ordered manifest/no-op rerun |
| CL-11 transactional migration | Populated source fixture, injected DDL/data/version/commit failures, rollback equality, crash/reopen unknown outcome, index/FK/history preservation; no implicit constructor DDL after migration refactor |
| CL-11 concurrency | Multi-process single active dispatch/claim, atomic review round updates, busy waits within budget, crash/block/reconcile and no automatic retry; local target filesystem/journal modes |
| CL-10.2/10.3 recovery | Completed backup, isolated restore, timed recovery, schema/data matching, release compatibility and controlled rollback without live file replacement |
| CL-12.6 deployment | Same physical volume, UID/permissions, explicit startup, WAL/checkpoint/locking on actual supported Linux and Docker Desktop storage; no cross-OS guarantee inferred from host tests |

Existing regression anchors are `tests/test_my_wfc.py:71,103,151,163,241,423`
(persistence, claims, duplicates, two-round history, partial-write rollback,
concurrent review submission), `tests/test_phase31.py` dispatch concurrency,
crash/idempotency and expiry cases, `tests/test_architecture_boundary.py:106`
repository injection, and `tests/test_my_wfc_packaging.py:10–30` path/resource
behavior. These tests use disposable fixtures. No new test or runtime experiment
is introduced here; they do not implement or certify the future runner, live
backup/restore, WAL compatibility or cutover.

Coder handoff in PR/chat reports actual git diff --check and python -m pytest -q
commands/results/environment, sole changed file, base/head SHA, remote push SHA,
PR URL and risks. Requested review effort is unspecified; model/effort evidence
unverified. Remaining risks: future schema drift, driver transaction semantics,
cross-OS locks, stale backup boundary, recovery data loss and uncertain external
effects. These require later evidence, not ADR authorship as proof.

Stop for Tech Lead exact-SHA review, maximum two formal rounds. Owner decides
merge. No self-approval, merge, new task record or CL-12.5 execution.
