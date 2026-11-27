# Controller recovery after uncertain dispatch

This guide describes evidence to preserve after a Controller timeout or uncertain
publication. The historical Phase 3.1 analysis below comes from the DOC-02
baseline `2048ea9383509bf389aff6bdb2a3f02fb2e922ef`. Confirm the deployed
revision and operator authority before taking any action.

## PostgreSQL Controller

The PostgreSQL-only development command is
`python -m agent_workflow.interfaces.cli.controller CONFIG --once`.
It requires an explicit `PG_RUNTIME_DSN` and a config without a SQLite `database`
field. The tracked Phase 3.1 example configs contain that historical field.
CI-only runs (`--ci-once` and `--ci-watch`) use the read-only Controller App
settings. Full dispatch also requires separate Worker credentials and dispatch
options.
The legacy SQLite Controller and its `--acknowledge` and `--reconciled` options
were removed in RET-05B.

The [PostgreSQL dispatch pass](../../src/agent_workflow/application/dispatch/run_dispatch_pass.py)
can leave the active slot held after leadership loss or leave an engine pin
after dispatch reaches `DONE` but terminal cleanup fails. The
[PostgreSQL ledger](../../src/agent_workflow/infrastructure/database/repositories/dispatch_ledger.py)
has an `acknowledge()` adapter method exercised in disposable tests. The
PostgreSQL CLI exposes no operator acknowledgement or recovery command.
SQLite rollback requires a separately verified operator procedure. If a
PostgreSQL incident or rollback depends on unverified recovery steps, preserve
the dispatch, engine, task and remote publication evidence and escalate to
the Owner and Tech Lead.

## Current PostgreSQL evidence

| Checkpoint | Current source | Focused tests |
| --- | --- | --- |
| Claim, eligibility, leadership and finish | [Dispatch use case](../../src/agent_workflow/application/dispatch/run_dispatch_pass.py), [Domain eligibility](../../src/agent_workflow/domain/dispatch/policy.py) and [PostgreSQL ledger](../../src/agent_workflow/infrastructure/database/repositories/dispatch_ledger.py) | [PostgreSQL dispatch lifecycle](../../tests/test_pg_dispatch_adapter.py) |
| Stage ordering, result validation and engine pin | [Execution service](../../src/agent_workflow/application/runtime/services/agent_execution.py), [Docker adapter](../../src/agent_workflow/infrastructure/runtime/docker_runtime.py) and [workspace adapter](../../src/agent_workflow/infrastructure/runtime/workspace.py) | [Phase regressions](../../tests/test_architecture_boundary.py), [cleanup budget](../../tests/test_phase31.py) |
| External commit, push and Draft PR | [Trusted Git worker](../../src/agent_workflow/workers/trusted_git/main.py) and [App authentication](../../src/agent_workflow/workers/trusted_git/app_auth.py) | [Worker publication](../../tests/test_architecture_boundary.py), [HTTP deadline](../../tests/test_http_deadline.py) |
| Local handoff and execution deadline | [Handoff use case](../../src/agent_workflow/application/task/use_cases/register_task_handoff.py) and [dispatch budget](../../src/agent_workflow/budget.py) | [PostgreSQL dispatch lifecycle](../../tests/test_pg_dispatch_adapter.py) |

The current Controller claims under leadership, then runs Prepare, Code, Test and
Publish. Publish can update GitHub before the Worker writes `result.json`.
Application settles each admitted phase, validates the result, records runtime
evidence, submits the handoff and tracks CI. Dispatch commits `DONE` before
clearing the admitted engine pin and releasing leadership. If cleanup fails after
finish, the dispatch stays terminal. If leadership is lost earlier, the active
slot may be retained. The ledger, the handoff and the GitHub branch and PR each
provide distinct evidence of how far the dispatch progressed.

## Historical SQLite uncertain-outcome matrix

**Historical Phase 3.1 behavior.** Stop/pause new Controller dispatch before inspection. Identify the **correct physical workflow SQLite DB and Docker endpoint/context**, then inspect the dispatch row with `slot=1` (status `ACTIVE` or `BLOCKED`), task/round/input SHA and relevant engine state. Phase 3.1 container evidence was **only correlational**: derived name `my-wfc-{dispatch_id}-{phase}`, shared label `my-wfc.phase=3.1`, the bind-mounted dispatch workspace path `WORKTREE/.runtime/dispatch/{dispatch_id}` (or its `source` subdirectory for Code/Test), and engine-reported creation/time/mount metadata. The label does **not** encode the dispatch ID, and **no immutable container ID is persisted in the dispatch ledger**. Names, labels, paths and creation time **do not independently prove ownership** or that a delayed Docker create has completed. The historical stop operation removed by derived **name**. If an operator cannot establish container identity and daemon completion from sufficient trusted external evidence, **the condition is unproven: retain the slot and escalate; do not run acknowledgement**. Verify remote branch head, PR head/body/author/base and local handoff/DB state; never automatically replay a potentially published job.

| Crash/uncertain boundary | Possible local observation | Remote effect that **may** already exist | Evidence / decision boundary |
| --- | --- | --- | --- |
| After `claim`, before or during Docker create | Dispatch still `ACTIVE` after abrupt death or `BLOCKED` if caught; slot=1 | No GitHub change expected this early, **but do not infer worker state from ledger alone** | Check exact dispatch ID/container names and task SHA, and reconcile any remaining worker before declaring stop; not an automatic retry |
| During Prepare, Code or Test, before Publish | `ACTIVE`/`BLOCKED`, no accepted `result.json` | Normally no publish yet; actual stage reached is uncertain after disconnect | Confirm stage/container lifecycle and artifacts; preserve slot on unknown status |
| During Git push or after push, **no existing Draft PR** | `ACTIVE`/`BLOCKED`; result missing | Remote branch may have moved even when no PR exists; push response may be lost | Check exact branch SHA/ancestry, App attribution, whether a PR was created after the last observation, and local handoff before considering any authorized intervention; **do not replay publish** |
| During Git push, **Draft PR already exists** | `ACTIVE`/`BLOCKED`; result missing; PR body may still show an old handoff SHA | An existing Draft PR tracks its branch: **PR head may already point at the newly pushed commit while PR body still describes the previous SHA** (PATCH follows push) | Inspect **branch head SHA, PR head SHA, PR author/base/draft status and PR body/handoff SHA** separately. A matching branch/PR head does not prove that the body/evidence update succeeded; keep the job blocked until reconciled |
| During or after **PR POST/PATCH**, before `result.json` | `ACTIVE`/`BLOCKED`; result missing | POST may have created a new bot Draft PR, or PATCH may have changed the body of an existing Draft PR **even when HTTP response was lost** | Query latest branch SHA, PR head SHA, draft/owner/base and PR body/handoff text independently; check for unexpected duplicate PRs and do not infer request failure from timeout or issue another POST/PATCH automatically |
| **After push**, existing PR lookup rejects unexpected PR | `ACTIVE`/`BLOCKED`; result absent | Branch may already have advanced; worker raises if more than one open PR, PR not draft or owner not expected bot | Compare exact remote branch and all matching open/closed PRs (including ownership/status) with the intended dispatch; rejection is **not** evidence push was rolled back; no replay without separate Owner-approved remediation |
| `WFC_RUNTIME_SECONDS` expires in a Worker phase (including Publish) | Entrypoint may return exit code **124**; Controller treats nonzero phase exit as a generic `BusinessRuleError`, and a caught error can leave the dispatch `BLOCKED` | During Publish, branch/PR mutation **may already have succeeded**; bounded child subprocess execution does not prove Docker daemon termination or absence of late create | Inspect remote effects and Docker engine state independently; do not interpret exit 124, deadline, or empty exact-name lookup as safe rollback or as authorization to retry |
| `result.json` written, **credential revoke fails** | Publish phase may exit nonzero before Controller reads the result; `ACTIVE`/`BLOCKED` | Branch and PR may be fully published **despite failed revoke/finalization** | Inspect remote branch, PR and token lifecycle evidence through authorized channels; never treat unsuccessful phase exit as proof of no publish or attempt automatic publish again |
| `result.json` present, before or during local handoff | Dispatch may still `ACTIVE`/`BLOCKED`, WFC task may remain `AWAITING_HANDOFF`/`CHANGES_REQUESTED` | Remote branch/PR may already be complete | Validate file provenance and exact SHA/PR against GitHub, then inspect WFC state; local result is worker-reported, not remote verification |
| After handoff commit, before dispatch `DONE` commit | WFC may have an exact-SHA `HANDOFF` event and `READY_IN_REVIEW`, **or have advanced to `IN_REVIEW` / a later review status** while dispatch remains `ACTIVE`/`BLOCKED` | Remote branch/PR may already exist | Check **both** SQLite handoff events (event type `HANDOFF`, associated task and current head SHA), task/head, review claims/history/verdict, dispatch row and exact GitHub SHA; do not classify a progressed review as missing handoff or republish solely because ledger is not `DONE` |
| Final `stop()` fails **after handoff commit but before `finish()`** | Handoff and potentially review history exist; `run_once` can mark dispatch `BLOCKED` with slot retained, despite successful WFC handoff | Remote branch/PR and handoff can be valid; failure is in cleanup before dispatch completion | Distinguish a **final cleanup exception** from a failure to commit `finish()`. Inspect engine/owned-container and late-create uncertainty separately, plus exact handoff/review evidence; do not infer the handoff failed, manually replay or acknowledge without complete operator proof |

**Historical acknowledgement behavior.** The retired SQLite CLI required an
operator assertion of prior reconciliation. It called Worker reconciliation
before updating the ledger. The ledger released the slot, retained the unique
`(task_id, round_number, input_sha)` key and recorded no durable operator
identity or evidence. Preserve unresolved `ACTIVE` or `BLOCKED` state and
escalate for a separately authorized procedure.

## Historical SQLite reconciliation outcomes

The historical SQLite ledger's `ACKNOWLEDGED` status means **only** that a slot was released after an operator assertion. It does **not** mean that the business task is finished, that GitHub changes are safe, that credentials were revoked, or that the old dispatch can run again. Before any authorized acknowledgement, capture an **operator-controlled incident record outside the ledger** identifying the correct DB/dispatch/task/round/input SHA, environment/engine evidence, remote branch and PR head/body/owner/base/draft, WFC task/handoff status, unresolved uncertainties and Owner's specific decision. The historical ledger had no field recording who inspected it.

| Verified investigation outcome | What Phase 3.1 could establish | Boundary / next decision |
| --- | --- | --- |
| Published remotely **and** a matching local WFC `HANDOFF` event is verified for the exact task/head SHA, whether task state is `READY_IN_REVIEW`, already `IN_REVIEW` or has since advanced through review | Independently compare the event sequence, current head, review claim/history/verdict and remote PR SHA; a later state is **not evidence that handoff was absent**. Record engine/container termination and late-create evidence for an Owner-authorized decision | Acknowledgement alone does not approve/merge, reset a review lease, undo a verdict, consume another review round or certify runtime safety. Never overwrite advanced review state merely to reconcile dispatch |
| Remote push/PR verified, **but handoff absent or conflicting** | **No general built-in repair flow is authorized by this guide**; retain the blocked dispatch and preserve source/remote evidence | Escalate for explicit task-specific Owner approval and independently reviewed repair method; do not call `register_task_handoff` manually, rewrite the ledger or replay publish based on this document |
| Independently observed **no publish**, with worker/container fully settled | Acknowledgement still leaves the **original key consumed** and the WFC task potentially `AWAITING_HANDOFF`/`CHANGES_REQUESTED` | No sanctioned same-key retry is implemented; escalate for a new Owner-approved continuation/recovery design. Do not edit SQLite, forge a new input SHA or burn a review round to force redispatch |
| Any remote/local/engine result **UNKNOWN** or contradictory | `ACTIVE` or `BLOCKED` remains a fail-closed operational stop | Do not acknowledge, retry, cleanup by guessed name, manually register a handoff or mutate task state. Escalate with evidence and missing checks |

**Historical limitations and untested boundaries.** `docker run` uses Controller-provided `WFC_RUNTIME_SECONDS` enforced by subprocess timeout in [`docker/coder_entry.py`](../../docker/coder_entry.py); when the subprocess times out, the entrypoint returns **124**, and Controller can surface this only as a nonzero phase failure. This self-bound is **not proof** that Docker daemon stopped or that Publish had no GitHub side effects (nor does it exclude late creation). In the historical path, **Prepare, Code and Publish had no explicit `--network=none`** (their effective network/egress policy must be verified at runtime); **Test** is explicitly offline. GitHub branch protection for `main` is **UNKNOWN** here; worker-side branch refusal does not prove server-side protection.

| Boundary requiring separate synthetic fault-injection evidence | Current test evidence limitation |
| --- | --- |
| Existing bot Draft PR `PATCH`, unexpected existing PR, closed PR, or post-push validation failure | Cited [worker publish tests](../../tests/test_architecture_boundary.py) use a fake GitHub App with empty PR lookup for the tested happy path; no cited test proves these branches safe |
| Lost/ambiguous `POST` or `PATCH` response, non-success Git push after remote ref update | Cited [HTTP deadline tests](../../tests/test_http_deadline.py) exercise synthetic GET/request process expiry, **not** a real or fault-injected remote GitHub mutation |
| Real container `stop()` sequence, delayed daemon create, wrong-name/ownership collision, result present but cleanup fails | [`tests/test_phase31.py`](../../tests/test_phase31.py) cleanup budget unit test **mocks** `docker_command`. A separate [`tests/live_phase31_smoke.py`](../../tests/live_phase31_smoke.py) **does exercise a real Docker engine** in a synthetic probe/test/timeout flow and removes containers by derived name; [historical coder handoff](../history/handoff-log.md) **reports** it passed with `TIMEOUT_STOP_CONFIRMED` in its earlier environment. **Live execution remains unverified for the current path.** That smoke catches generic `BusinessRuleError` and does **not** prove immutable ownership, late daemon create exclusion, or that the specific child self-timeout path occurred |
| Revoke failure after `result.json`, final `stop()` failure after handoff, `register_task_handoff` success followed by `finish` failure, or review advancing while dispatch slot remains | Not established by the cited focused tests; requires explicit synthetic stage-finalization, engine failure, parallel review and two-transaction crash tests |

These are **test/backlog gaps**, not permission for a documentation-only agent to run fault injection, live engine commands, credentials or production recovery.

## TARGET contract (not CURRENT code)

The [merged CL-12 architecture ADR](../adr/CL-12-architecture.md) selects a separate unprivileged Linux-host Trusted Executor using local Rootless Podman CLI, with authenticated bounded Controllerâ†’Executor IPC, durable job identity, isolated Prepare/Code/Test/Publish stage containers and fail-closed unknown outcomes. [Executor security ADR Â§3](../adr/CL-12-executor-security.md) sketches `submit/status/stop/reconcile` and replay semantics. **These are design requirements, not implemented APIs.** [CL-12.5 security ADR](../adr/CL-12-executor-security.md) and deployment/consolidated ADRs now state their **post-merge documentation status**; their approval does not close parent, implementation or production gates.

**Still open for a separately approved Executor Tech Spec / fault-injection work:** IPC transport and caller identity verification; exact durable-write checkpoints relative to container creation, Git push, PR creation and handoff; source of truth when Controller/Executor records disagree; authoritative publish reconciliation; operator `reconcile` privilege; crash/unknown-result states and retry policy. Require synthetic nonproduction tests for each side-effect boundary, without live credentials or reusing Phase 3.1 data.

## Read/analysis hygiene

1. Start at [documentation index](../INDEX.md) and this focused route, then inspect exact symbols, small line ranges and matching tests.
2. Check the current `main` SHA first; the source paths in this guide are navigation hints, **not** a claim that function behavior or deployment is permanently unchanged.
3. In a handoff, label each conclusion **SOURCE FACT / DESIGN DECISION / REPORTED TEST / UNKNOWN** and distinguish synthetic unit tests from real GitHub App, Docker, WSL2 or native Linux tests.
4. For any live operational action, STOP and use the Owner-authorized incident/recovery runbook and verified environment, not solely this document. Do not print credentials or raw sensitive worker output.

Current strict offline sandbox transport and detached watchdog are owned by `infrastructure/runtime/sandbox.py` and `infrastructure/runtime/sandbox_watchdog.py`; the watchdog module is launched as `agent_workflow.infrastructure.runtime.sandbox_watchdog`. The relocation does not establish real Docker conformance; live watchdog, engine-identity and cleanup evidence requires an authorized disposable Linux engine/image. Treat this evidence as NOT VERIFIED until run.
