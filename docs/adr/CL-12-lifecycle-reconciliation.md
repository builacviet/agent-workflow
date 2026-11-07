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

### Pinned evidence index — CURRENT source versus selected TARGET

All source links below are locked to DOC-03 merge `abf8863502714c4893350fd016609ab91f3cde0d`, not the latest working tree. They demonstrate code flow, **not** a running host's behavior.

| Class | Specific evidence at pinned SHA | Interpretation |
| --- | --- | --- |
| CURRENT | [Controller `run_once` lines 23–49](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/my_wfc/controller.py#L23-L49); [acknowledge lines 104–117](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/my_wfc/controller.py#L104-L117) | Handoff and ledger finish separated; stop precedes acknowledgement validation |
| CURRENT | [Dispatch `claim` 29–72](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/my_wfc/dispatch_repository.py#L29-L72); [`finish/block/ack` 75–100](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/my_wfc/dispatch_repository.py#L75-L100) | Single slot and spent key; no per-stage durable handles |
| CURRENT | [Docker CLI `run_container` 162–197](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/my_wfc/coder_runtime.py#L162-L197); [name-based `stop` 204–233](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/my_wfc/coder_runtime.py#L204-L233); [handoff/finally 389–401](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/my_wfc/coder_runtime.py#L389-L401) | CLI exit does not settle daemon effects; final stop may fail after handoff |
| CURRENT | [Manual handoff `service.py` 115–179](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/my_wfc/service.py#L115-L179) | No shared dispatch-fence comparison with admission |
| CURRENT | [Worker push, Draft PR and result 140–202](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/coder_worker/main.py#L140-L202); [token revoke 205–223](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/src/coder_worker/main.py#L205-L223) | Push precedes PR; results and revocation can diverge |
| TARGET ADR | [CL-12.7 stage lifecycle and remote truth 75–83](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/docs/adr/CL-12-architecture.md#L75-L83); [CL-12.7 Executor boundary 54–71](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/docs/adr/CL-12-architecture.md#L54-L71) | Stage result then reap **before next stage**; remote reconciliation and typed IPC are design targets |
| TARGET ADR | [CL-12.5 owned job authority 42–65](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/docs/adr/CL-12-executor-security.md#L42-L65); [CL-12.4 shared physical SQLite and recovery 109–151](https://github.com/builacviet/techlead-review-poc/blob/abf8863502714c4893350fd016609ab91f3cde0d/docs/adr/CL-12-persistence.md#L109-L151) | Authenticated job requests; one verified physical DB, separately authorized migration |
| UNKNOWN | Effective DB generation/journal/mount, container endpoint, GitHub/App write restrictions, token custody and native Linux isolation | Requires operational evidence at separate Owner gate; not proven here |

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

**Split-ledger reconciliation rule (proposal).** WFC + dispatch must use **one independently verified, currently opened physical SQLite database and transaction authority**; matching path strings are insufficient. Only that transaction authority may grant admission and automated review eligibility. A separate durable Executor journal owns engine command/handle/settlement records and is queried over authenticated typed IPC, never written directly by Controller. Correlate both with immutable environment/DB-generation, job ID, dispatch ID, stage attempt and policy digest. Neither ledger may overwrite the other's history when observations differ. Controller's cached state or an Executor `DONE` alone cannot release a slot or grant review. On mismatched journal, restart, restore, generation change or missing intent, **stop admissions, retain/restore a conservative fence, verify engine and GitHub effects independently and escalate**. A DB restore can discard local intent while remote effects survive; restoring an older DB does not prove cancellation. Exact DB generation/restore policy and durable external reconciliation registry remain Owner decisions D-02A/D-04; no live restore is authorized.

## 3. Stable identity, durable writes and invariants (TARGET proposal)

### 3.1 Identity binding, per-stage journaling and proof of settlement

Define an immutable, operator-authorized **job identity** tied to: `task_id`, exact `round_number`, `input_sha`, `dispatch_id`, authorized repository/branch/base, environment and **physical DB generation**, approved workspace ID, policy version and image manifest digest. Each stage attempt additionally binds stage enum (Prepare, Code, Test, Publish), unique attempt ID, request digest, engine namespace/endpoint identity and trusted artifact reference. Persist this correlation in the Controller/dispatch ledger and Executor's independent journal with an authenticated identity-bound request; schema and transport await separately approved CL-11/T02 decisions.

**Each of the four stages must satisfy this ordered write/effect contract, not only the final stage:**

1. **CREATE_INTENT durably journaled by Executor** with attempt, approved engine namespace, immutable launch policy, container-create identity request and deadline **before** issuing create. Controller records the authorized stage/request intent before submitting; an uncertain IPC submit keeps the Controller fence.
2. **CREATE outcome:** if accepted, store the exact immutable engine/container ID and matched creation evidence in Executor journal **before issuing START**. If response is lost or journal commit fails after engine acceptance, classify `UNKNOWN_CREATE`; reconcile authoritative engine events/owned ID under the verified namespace. A guessed name, temporary absence, another process exit or late timeout can never settle the create. No automatic second create.
3. **START_INTENT durably journaled** against that persisted exact handle **before START**, then confirm/persist engine start acceptance or `UNKNOWN_START`. Lost start response does not authorize repeat or start of the next stage.
4. **RUN/EXIT and outputs:** persist engine-observed terminal result and trusted bounded artifact evidence; record a distinct `STOP_INTENT` if cancellation/termination is requested. Persist stop request before any stop command. Reconcile lost stop response against **the exact immutable instance**, including engine lifecycle events. `CLIENT_EXITED` is not container settlement.
5. **REAP_INTENT and settlement:** persist removal intent, validate owned engine/handle against the journal, reap **only the verified immutable instance**, confirm terminal/non-running and absence with authoritative engine evidence, then durably record `STAGE_SETTLED`. If a create/start/stop/reap command may still arrive late, or engine journal/namespace differs, retain `UNKNOWN_*` and the dispatch fence. A single negative container listing cannot prove no future effect; release needs terminal authoritative evidence excluding outstanding commands or an Owner-approved quarantine resolution. **Next stage may start only after previous stage is settled**, its artifacts verified, and its auth/credential disposition passes the stage gate. Persist stage-specific completion independently, not only job-level status.

Executor is the **only** proposed owner of engine command journal and immutable handles; Controller receives typed attested status and independently correlates its own dispatch record. Exactly how a crash between effect acceptance and journal persistence can be reconciled (engine event/log retention, authenticated reconciliation owner, late-command quiescence) is an **unresolved acceptance requirement**, not evidence currently available. If settlement cannot be proven, the safe state remains blocked rather than fabricating an ID or declaring no effect.

The CURRENT `docker run` attached process and derived-name cleanup do **not** implement this TARGET create/start/reap contract.

### 3.2 Safety invariants (I01–I12)

1. **I01 — Idempotent admission.** Same authorized job+payload gets its previously durable status; changed payload with same identity is rejected. Same task/round/input key never silently becomes a new job after ACK; no automatic same-key replay.
2. **I02 — One active authoritative assignment.** A task/round/input has at most one active automated dispatch. An overlapping manual handoff is fenced, except via explicit Owner-approved override with settled effects.
3. **I03 — Durable intent before effect.** Each stage's create, start, stop and reap requires its own operation/attempt intent committed by trusted Executor **before** the external engine action. Each separate push, PR POST and PATCH requires its own committed mutation intent. A failed or unknown persistence outcome prohibits further effects.
4. **I04 — Owned-only cleanup.** Validate physical DB, engine namespace and immutable owned handle against durable intent **before any destructive action**. If identity or delayed-create status is unknown, never delete or release based on name/label alone.
5. **I05 — Immutable tested artifact.** Trusted manifest binds approved path/type/mode/bytes and trusted Test policy/result to the exact artifact considered for Publish. No Publish if changed or unverified; details belong to M10/M11.
6. **I06 — Publish authorization fence.** Establish a durable single-writer task/branch lease and check its generation and exact task/round/input/dispatch immediately before **each** separate push, PR POST and PR PATCH. An override, expiry or revoke cannot declare the old authorization settled while an already-sent request may still mutate remote state; mutation stays UNKNOWN and fenced until reconciled. Revalidate expected ref/PR under an Owner-approved enforceable branch writer policy (a read alone is not an atomic GitHub CAS).
7. **I07 — Remote truth.** Worker HTTP/push response, exit status or `result.json` alone does **not** establish successful Publish. Independently re-query exact GitHub ref, PR head/base repositories, draft/actor and evidence body against intended commit/branch/policy.
8. **I08 — Unknown stays blocked.** Lost create/start/stop/reap/push/PR response, conflicting ref, unresolved stage or unverifiable artifacts never trigger automatic destructive cleanup, duplicate publish, handoff repair or review eligibility. **Unknown credential grant/revoke is a separate security gate:** independently verified publication may remain `PUBLISH_VERIFIED`, but automated finalization, review eligibility **and slot release stay blocked** until the token is confirmed revoked, or expiration plus acceptable containment evidence is established, or an explicit Owner-approved security disposition permits the transition. A worker's revoked flag or missing token cache alone is insufficient.
9. **I09 — Local atomic completion.** Automated `dispatch DONE`/slot release and WFC `READY_IN_REVIEW` handoff must commit in one compare-and-set **SQLite transaction over the same verified physical DB**, binding exact dispatch/round/input, immutable finalization ID, verified remote receipt, all settled stages **and resolved credential disposition**. A provisional handoff, if designed, is not reviewable. If commit acknowledgement is lost, re-read immutable finalization/event history by ID in the correct DB generation; do not repeat write or infer failure from stale status (review or next dispatch may have progressed).
10. **I10 — Manual/automated isolation.** Manual handoff follows its own authorized contract; it cannot silently replace an active automated job or consume a correction round. Historical already-progressed reviews are preserved during reconciliation.
11. **I11 — Review governance.** Exact-SHA claim, durable review/finding history, two-round maximum, independent remote freshness check and Owner-only merge are preserved. Executor and Worker cannot approve reviews.
12. **I12 — Auditable operator reconciliation.** Every authorized release/override records authenticated operator identity, immutable evidence references, outcome classification, affected DB-generation/job/engine/remote identifiers, consent/scope and timestamp. Slot release must be a conditional transaction on the **exact still-held dispatch** that also records the disposition; a changed/missing owner or unknown stage/token/remote truth means no release. No evidence → no release.

### 3.3 TARGET state model (logical names, NOT existing DB schema)

Per stage, repeat **`CREATE_INTENT → CREATED_HANDLE_PERSISTED → START_INTENT → RUNNING → EXIT_EVIDENCE → STOP/REAP_INTENT → STAGE_SETTLED`**. An already naturally exited stage still requires verified reap and settlement; a cancellation adds `STOP_INTENT` before engine effect. Any commit/effect discrepancy enters `UNKNOWN_CREATE/START/STOP/REAP`; keep the fence. Each stage must settle **before** advancing to the next, including the last Publish stage.

At the dispatch level: `AUTHORIZED → CLAIMED → {stage cycles} → PUBLISH_INTENT → [REMOTE_PARTIAL | PUBLISH_UNCERTAIN | PUBLISH_VERIFIED] → CREDENTIALS_SETTLED → LOCAL_FINALIZED(DONE + READY_IN_REVIEW)`. A verified remote branch advance with missing/stale Draft PR is **REMOTE_PARTIAL**, not successful publish or verified no-effect. On UNKNOWN, preserve the global exclusion fence. A separate authorized operator disposition may release it only with proved stage/remote/credential settlement; the original input key stays spent. No normal retry may invent a newer job ID for the same uncertain side effects.

Current `ACTIVE/BLOCKED/DONE/ACKNOWLEDGED` ledger states and WFC review statuses are **not** the proposed stage states. `CLIENT_EXITED`, `STOP_REQUESTED` or an empty engine query is insufficient proof of `STAGE_SETTLED`.

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
