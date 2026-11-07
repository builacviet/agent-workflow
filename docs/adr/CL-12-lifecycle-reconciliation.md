# M01 — Lifecycle, ownership and reconciliation contract (PROPOSED)

**Status:** DRAFT FOR OWNER DECISION; **documentation-only**, not accepted runtime behavior, a new recovery command, a schema migration or implementation authorization. **Baseline:** `main` at `abf8863502714c4893350fd016609ab91f3cde0d` (DOC-03 Owner merge, 2026-11-07). **Task:** [M01](https://app.notion.com/p/411f92014abd8197b2b4fad808d59230). **Input:** Owner-supplied independent ARCH-AUD-01, findings F01–F05 P1; [consolidated CL-12 TARGET](CL-12-architecture.md), [Executor security TARGET](CL-12-executor-security.md), and [CURRENT incident guide](../guides/dispatch-recovery.md).

This document specifies **candidate TARGET behavior** to be challenged and signed off by Owner; it deliberately does not claim any new implementation, executed test, verified live database, Docker/Podman isolation, GitHub branch protection or production readiness. Existing Phase 3.1 is preserved. See [M01 acceptance contract](../tasks/M01.md).

## 1. CURRENT facts and problem boundary (not proposed fixes)

- [`DispatchLedger.claim/finish/block/acknowledge`](../../src/my_wfc/dispatch_repository.py) persists one `slot=1` and a unique `(task_id, round_number, input_sha)` key. It has no per-stage lifecycle, immutable container handle or approved continuation/replay path. `ACKNOWLEDGED` releases only the slot; the key remains spent.
- [`controller.py::operate`](../../src/my_wfc/controller.py) calls `CoderRuntime.stop(dispatch_id)` **before** ledger acknowledgement validates that a slot belongs to the given ID. [`CoderRuntime.stop`](../../src/my_wfc/coder_runtime.py) finds/removes Docker containers by a derived name, without verifying immutable engine/container ownership. A negative name lookup does not prove delayed daemon create cannot occur.
- [`coder_worker.main::publish`](../../src/coder_worker/main.py) commits, ordinarily pushes, then GETs/POSTs/PATCHes Draft PR; response loss or rejection can occur after remote change. `result.json` and WFC handoff are local, worker-reported results, not authoritative current GitHub truth.
- [`coder_runtime.py::execute`](../../src/my_wfc/coder_runtime.py) commits `register_task_handoff` and subsequently performs a final cleanup; [`controller.py::run_once`](../../src/my_wfc/controller.py) commits `ledger.finish` separately. Review eligibility may appear before cleanup or `DONE` while the slot is still `ACTIVE/BLOCKED`.
- [`WorkflowService.register_task_handoff`](../../src/my_wfc/service.py) is also exposed through a mode-dependent MCP tool (authenticated by default; the explicitly unsafe POC bypass is separately documented). A manual report can race with an in-flight automated dispatch. WFC exact-SHA/two-round policy is valuable and must be preserved, not inferred to prove remote freshness.
- The CURRENT host Controller invokes Docker CLI directly. The separately selected TARGET is a restricted authenticated Controller-to-host-Executor interface and unprivileged Rootless Podman CLI, **not** a working implementation.

## 2. Authority and truth sources (candidate TARGET contract)

| Authority | Owns | Cannot authoritatively establish alone |
| --- | --- | --- |
| Owner | Approval of task scope, role/branch policies, exceptional recovery disposition, merge and cutover | GitHub remote state or engine settlement without evidence |
| WFC domain + local transaction store | Task authorization, manual/automated mode, exact-SHA handoff/review, immutable verdict history and two rounds | Current remote GitHub ref, PR or running container |
| Dispatch lifecycle store / Controller | Admission, durable job/dispatch/round fence, stage intent and local finalization | Host engine/container ownership or remote publish success by itself |
| Dedicated trusted Executor (TARGET) | Versioned stage policy, immutable engine identity/owned lifecycle, verified stage state/artifacts; typed status/stop/reconcile evidence | Approval of PR, third review round, invented GitHub success |
| Trusted Worker Publish + independent read-only verifier | Intended commit, ordinary Git push, Draft PR mutation and checked GitHub ref/PR receipt | Local DB commit/review status after process failure |
| GitHub | Actual branch ref, commit ancestry, PR head/base/repositories/draft/actor/body | Whether WFC task or local dispatch was finalized |

**No distributed ACID transaction is claimed across SQLite, engine and GitHub.** Success must be derived from durable local intent plus **independent evidence** of each external effect. Failures with ambiguous outcomes stay fenced and require explicit operator reconciliation; a timeout or process exit is not rollback evidence.

## 3. Stable identity, durable writes and invariants (TARGET proposal)

### 3.1 Identity binding

Define an immutable, operator-authorized **job identity** tied to: `task_id`, `review_round/round_number`, exact `input_sha`, `dispatch_id`, authorized repository/branch/base, tenant/environment/physical-DB identity, approved workspace ID, policy/version and image manifest digest. Each stage attempt additionally binds stage enum, deterministic request digest, distinct attempt identity, engine namespace/endpoint identity and a host-owned artifact manifest reference. The specific schema and IDs require separately approved CL-11 implementation; **syntax alone, shared labels, derived names and paths are not ownership proof**.

Persist an immutable **stage intent before asking the engine to create anything**. After a successful engine create response, persist returned immutable engine/container handle (scoped to the verified engine namespace) and stage outcome. If create may have been accepted without a returned handle, record **UNKNOWN_CREATE** with the dispatch fence still held; no automatic relaunch. Recover only through trusted engine events/strong ownership evidence or escalate if inconclusive. Do not clean by a guessed name.

### 3.2 Safety invariants (I01–I12)

1. **I01 — Idempotent admission.** Same authorized job+payload gets its previously durable status; changed payload with same identity is rejected. Same task/round/input key never silently becomes a new job after ACK; no automatic same-key replay.
2. **I02 — One active authoritative assignment.** A task/round/input has at most one active automated dispatch. An overlapping manual handoff is fenced, except via explicit Owner-approved override with settled effects.
3. **I03 — Durable intent before effect.** Create/stop and remote GitHub mutations cannot begin before a record of intended operation, identity and deadline is durable.
4. **I04 — Owned-only cleanup.** Validate physical DB, engine namespace and immutable owned handle against durable intent **before any destructive action**. If identity or delayed-create status is unknown, never delete or release based on name/label alone.
5. **I05 — Immutable tested artifact.** Trusted manifest binds approved path/type/mode/bytes and trusted Test policy/result to the exact artifact considered for Publish. No Publish if changed or unverified; details belong to M10/M11.
6. **I06 — Publish authorization fence.** Immediately before irreversible GitHub mutation, revalidate task, round, input SHA, dispatch ownership, expected branch policy and release/test evidence. A stale/overridden job cannot publish.
7. **I07 — Remote truth.** Worker HTTP/push response, exit status or `result.json` alone does **not** establish successful Publish. Independently re-query exact GitHub ref, PR head/base repositories, draft/actor and evidence body against intended commit/branch/policy.
8. **I08 — Unknown stays blocked.** Lost create/stop/push/PR response, conflicting ref, revoke failure or unverifiable artifacts never trigger automatic destructive cleanup, duplicate publish, handoff repair or review eligibility.
9. **I09 — Local atomic completion.** Automated `dispatch DONE`/slot release and WFC `READY_IN_REVIEW` handoff must commit in one validated **local** transaction **after** stage settlement and accepted remote receipt. A provisional handoff, if designed, is not reviewable.
10. **I10 — Manual/automated isolation.** Manual handoff follows its own authorized contract; it cannot silently replace an active automated job or consume a correction round. Historical already-progressed reviews are preserved during reconciliation.
11. **I11 — Review governance.** Exact-SHA claim, durable review/finding history, two-round maximum, independent remote freshness check and Owner-only merge are preserved. Executor and Worker cannot approve reviews.
12. **I12 — Auditable operator reconciliation.** Every authorized release/override records operator identity, immutable evidence references, outcome classification, affected job/engine/remote identifiers, consent/scope and timestamp. No evidence → no release.

### 3.3 TARGET state model (logical names, NOT existing DB schema)

`AUTHORIZED → CLAIMED → STAGE_INTENT → STAGE_RUNNING → STAGE_SETTLED → PUBLISH_INTENT → PUBLISH_UNCERTAIN / PUBLISH_VERIFIED → LOCAL_FINALIZED → DONE`.

At every external boundary, the state may instead become `BLOCKED_UNKNOWN` while the global exclusion fence remains held. `PUBLISH_UNCERTAIN` is **not** approval to retry the POST/PATCH or push. `LOCAL_FINALIZED` includes one transaction binding exact dispatch and task/head to both `DONE` and review eligibility; naming may be refined during CL-11 task/schema design. A separate operator reconciled disposition may release admission only if all required proofs and approvals are satisfied; releasing the slot does not make the original key reusable.

Current `ACTIVE/BLOCKED/DONE/ACKNOWLEDGED` ledger states and CURRENT WFC statuses are distinct from these **proposed** logical states. Do not equate a stage `STOP_REQUESTED` or `CLIENT_EXITED` with a verified settled engine instance.

## 4. Checkpoint ordering (TARGET proposal, not a runnable procedure)

| Checkpoint | Persist before external effect | Required post-effect verification | On unknown |
| --- | --- | --- | --- |
| A — Admission | Approved assignment + one durable dispatch/round/input fence | Re-read exact WFC authorization | Hold slot; no new launch |
| B — Stage create | Per-stage intent, engine/environment, policy, stage, request digest | Immutable handle and engine event/exit evidence | UNKNOWN_CREATE; do not relaunch or infer absence |
| C — Stage complete | Artifact/test evidence bound to stage+job | Trusted digest, policy, ownership and exit | BLOCKED, no Publish |
| D — Pre-publish | Exact intended commit/parent, expected remote ref, PR identity/policy, job+artifact/test receipt | Recheck authorization and GitHub write preconditions | BLOCKED, no mutation if stale |
| E — Git push | Same durable publish intent and expected ref | Independently read target branch SHA and ancestry | BLOCKED, no automatic second push |
| F — Draft PR POST/PATCH | Durable intended mutation and existing PR decision | Independently read PR repo/head/base/draft/author/body and ref | BLOCKED, no blind POST/PATCH repetition |
| G — Credential revoke | Durable token lifetime/disposition reference without secrets | Authorized expiry/revoke evidence; do not equate with remote rollback | Flag separate credential uncertainty; remote publish status independently retained |
| H — Final stage stop | Durable owned stage handle and stop intent | Exact immutable instance no longer running + engine settlement | BLOCKED; no local finalized/reviewable |
| I — Local completion | Validated exact remote receipt, trusted test artifact and settled stages | Single guarded SQLite commit: ledger DONE + WFC reviewable event/head; re-read | Read local transaction outcome; never forge handoff, mutate history or replay |

**GitHub branch policy remains an Owner decision:** normal push and a read-before-write check alone do not provide atomic compare-and-swap against another writer. Require branch-writer/protection constraints and test effective behavior; no force push. A request to `get_status` or reconcile evidence is not a publish retry.

## 5. Manual/automated assignment and review eligibility (TARGET proposal)

- Admission selects **automated** ownership only for the exact authorized task/round/input. The MCP manual handoff path must check this fence and reject competing manual progress while the automated job is active or side effects remain unknown.
- A task-specific Owner override is a **new auditable authorization**; it does not imply that the old executor stopped or that GitHub did not change. Reconcile and settle the old job before changing ownership or eligibility.
- The automated Worker cannot set `READY_IN_REVIEW` through a standalone handoff write. The trusted local finalizer validates the exact dispatch, expected WFC status/round/input and remote receipt, then atomically records `DONE` and eligible handoff.
- **Manual tasks:** preserve the existing manual submission/review workflow under explicit identity checks and independent remote verification. Do not automatically bind manual handoffs to an Executor job that never existed.
- **Historical progression:** when CURRENT already recorded `HANDOFF` and later `IN_REVIEW`/verdict with a blocked dispatch, the reconciler must recognize the immutable event/claim/review history and choose an evidence-backed disposition. It must never reset a verdict, consume a review round as a repair, or republish by guessing.

## 6. Operator-only reconciliation matrix (TARGET policy proposal)

Every case requires verification of the correct **physical DB, environment, engine ownership and settled/unknown stage lifecycle**. Preserve logs/intent without credentials; the current CLI does not implement these gates.

| Investigation result | Required evidence | Disposition proposed; never automatic |
| --- | --- | --- |
| Remote ref + Draft PR match intended receipt; matching WFC handoff/review exists | Exact task/input/output SHA, PR repo/head/base/actor/draft/body, handoff event/head/review history and settled owned stages | Authorized operator may consider releasing a blocked slot after storing evidence. Preserve any already-progressed review; do not repeat handoff or publish |
| Remote published but WFC handoff absent/conflicting | GitHub receipt, dispatch intent, WFC task/event absence/conflict, engine settlement | Keep BLOCKED; Owner must approve a separately reviewed repair/continuation protocol; never call manual `register_task_handoff` as an invented fix |
| Verified no remote mutation and no pending engine effect | Independent remote ref/PR history and fully settled owned engine stages, with no unresolved create | Existing key still spent; keep evidence and escalate for Owner-approved new continuation design; never force new input SHA, DB reset or same-key auto retry |
| Remote ref, PR, token or stage state UNKNOWN or contradictory | Records of attempted checks, missing evidence and known partial effects | Fail closed: no acknowledgement, delete, retry, merge, provisional handoff acceptance or review activation |
| Publish succeeded; credential revoke failed or uncertain | Independent remote receipt, grant/token expiry evidence and settled stage checks | Keep published outcome distinct from token risk; escalate credential disposition/rotation under authorized security policy; do not re-publish |
| Local handoff + review progressed before dispatch DONE (CURRENT historical) | Event SHA, claims, review rounds/verdict, dispatch ledger and remote evidence | Preserve immutable review history; reconcile pending slot without rewriting verdict. Requires a task-specific reviewed plan until TARGET finalization exists |

**Current CLI limitation:** The existing `--acknowledge --reconciled` performs name-based cleanup before ledger validation, stores no immutable actor/evidence and does not implement this TARGET matrix. **Do not run it based solely on this draft.**

## 7. M02 characterization/negative-test contract (future, separately approved)

Tests must be deterministic on synthetic fake engine/GitHub transport and disposable SQLite fixtures. Characterize CURRENT behavior first and label known defects; then reuse as TARGET acceptance tests. **Test execution in M01: NOT RUN.**

1. Wrong DB, dispatch, engine context, forged/unknown 32-hex ID, name collision: **zero destructive calls**.
2. Daemon accepted create but response was lost; delayed create after empty name lookup, crash before handle persistence: no automatic relaunch/cleanup/release.
3. Crash/restart at each durable checkpoint; identical status queries are idempotent, changed duplicate jobs conflict.
4. Pre-publish authorization/round/head changes and overlapping manual handoff: no stale publish or review mutation.
5. Push accepted with response lost; Draft PR POST/PATCH accepted with response lost; unexpected/closed/duplicate PR; separate ref and PR truth.
6. Test artifact modified after Test or handoff evidence forged: no remote write, no review eligibility.
7. Result written then revoke fails, Publish exits 124, stop fails after handoff and lost ledger completion: remote/local state remains classifiable, no duplicate mutation or review-round loss.
8. Concurrent review claims and existing progressed review during dispatch reconciliation: history remains immutable and exact SHA bound.
9. Uncertain/contradictory engine or GitHub outcome: fence remains; any operator path requires verified actor and recorded disposition.
10. Future Executor IPC: wrong caller/nonce/replay, oversized or invalid fields, stale policy/attempt and arbitrary mount/flag: reject with zero engine effects (separately implemented/tested).

M02 is **tests-only** with no actual host Docker/Podman/GitHub mutations or POC DB use. Native Linux stage/security tests are separate downstream acceptance gates, not passed by these fixtures.

## 8. Owner decisions required BEFORE accepting this spec

| ID | Proposed default — **NOT APPROVED** | Decision needing Owner sign-off |
| --- | --- | --- |
| D-01 Scope/governance | M01 documentation-only and independent exact-SHA review; no implementation, merge or cutover inherited | Sign off this contract and per-task implementation authority separately |
| D-02A Durable lifecycle source of truth | Local dispatch store owns admission/fence; trusted Executor owns immutable stage evidence; GitHub remains remote truth; reconcile by typed records/receipts, not a shared privileged DB | Authoritative identity format, storage boundaries, write-ahead checkpoints and operator evidence owner |
| D-02B Safe acknowledgement/recovery | Unknown create/remote outcomes always retain fence; only authenticated authorized operator can release with durable evidence; no automatic same-key replay | Who can reconcile, which proven dispositions permit slot release, how a verified no-effect job continues |
| D-02C Review gating | Automated WFC eligibility becomes visible only in same local transaction as dispatch DONE after accepted remote receipt and stage settlement; manual handoff kept distinct | Whether provisional nonreviewable handoff exists; treatment of already-progressed historical reviews |
| D-03A GitHub writer policy | One approved branch writer; ordinary push only, no force; verify protected branch namespace/actual App grants and expected-ref behavior | Branch protection/rulesets, competing manual writer policy and permitted conflict remediation |
| D-03B Publication evidence | Intent before remote effect; read-only verification of exact ref/PR; BLOCKED if mutation outcome unclear, no automatic repeat | Exact evidence schema and actor permitted to attest remote state |
| D-03C Stage credentials | Prepare least-privilege read, Publish scoped write; distinguish revoke status from Publish truth | App permissions, token owner, revoke/expiry policy and whether exceptions require new ADR |

Remaining design issues (CL-11 schema migrations/physical DB, M10/M11 artifact integrity, rootless Executor transport, native Linux validation) remain separately approved dependencies; accepting M01 **does not implement them**.

## 9. Downstream gates and scope integrity

- **M02** characterization harness follows Owner-approved M01 contract; its tests are not run by this document task.
- **M03–M09** implement DB/lifecycle/manual-vs-auto/publish/finalization behind isolated migrations and review; **M10/M11/M12** handle sealed artifacts, trusted Test and credentials; **T01–T09** implement TARGET Executor/operations only under separate authorization.
- Preserve current `order_service` invariants, exact-SHA two-round reviews, no force push/auto-merge, single-slot safety before measured concurrency, and refusal to auto-retry an unknown publish.
- For M01 itself, only [this ADR](CL-12-lifecycle-reconciliation.md) and [task contract](../tasks/M01.md) may change. No source/tests/DB/Compose/CI/credentials/engine/production modifications. Require path/link verification, exact-base/head diff and independent review; maximum two rounds; **Owner alone decides merge and final spec acceptance**.
