# Phase 3.1 CURRENT — dispatch timeout and uncertain GitHub effects

**Status:** source-linked **read-only investigation aid** for the existing Phase 3.1 Controller/Docker/Worker workflow, as observed in repository `main` at DOC-02 baseline `3c096d06124a5c5a40f9328ec6e28109d4dbefe3` (2026-11-07). **Not** a new tested recovery procedure, automation, Linux-host Executor implementation or permission to operate a live system. Validate actual deployment, exact source SHA and operator authority before acting.

Start with [AGENTS rules](../../AGENTS.md) and the authorized incident/task. This guide routes an agent to the source/test evidence for a specific timeout/disconnect scenario. Do **not** read all historical documentation.

## Minimal call path: where to inspect

| Checkpoint | CURRENT source and symbols | Focused existing tests |
| --- | --- | --- |
| Admission/claim and failure | [`src/my_wfc/controller.py`](../../src/my_wfc/controller.py): `run_once`, `operate`; [`src/my_wfc/dispatch_repository.py`](../../src/my_wfc/dispatch_repository.py): `DispatchLedger.claim/block/finish/acknowledge` | [`tests/test_phase31.py`](../../tests/test_phase31.py): `test_atomic_global_slot_across_connections`, `test_expiry_restart_and_failure_never_auto_redispatch`, `test_runtime_exception_blocks_slot_and_sanitizes_ledger` |
| Docker lifecycle and staged result | [`src/my_wfc/coder_runtime.py`](../../src/my_wfc/coder_runtime.py): `run_container`, `CoderRuntime.stop/docker/execute` | [`tests/test_phase31.py`](../../tests/test_phase31.py): stage, timeout, cleanup checks; choose exact test function by symbol search, not guessed line number |
| External commit / push / draft PR | [`src/coder_worker/main.py`](../../src/coder_worker/main.py): `publish`; [`src/coder_worker/app_auth.py`](../../src/coder_worker/app_auth.py) HTTP lifecycle | [`tests/test_http_deadline.py`](../../tests/test_http_deadline.py): `test_http_total_deadline_kills_and_reaps_request_worker`, `test_trickle_timeout_runs_cleanup_and_blocks_dispatch` |
| Local handoff vs dispatch completion | [`src/my_wfc/service.py`](../../src/my_wfc/service.py): `register_task_handoff`; [`src/my_wfc/dispatch_repository.py`](../../src/my_wfc/dispatch_repository.py): `finish` | [`tests/test_phase31.py`](../../tests/test_phase31.py): `test_success_handoff_and_idempotency` |
| Clock and transaction mechanics | [`src/my_wfc/budget.py`](../../src/my_wfc/budget.py); [`src/my_wfc/repository.py`](../../src/my_wfc/repository.py): `raw_transaction` | Read only when deadline/transaction semantics are under investigation |

**Ordering established from code:** Controller acquires persistent `ACTIVE` dispatch slot → runs Prepare/Code/Test/Publish → Publish may commit/push/create or update a Draft PR on GitHub **before** writing `result.json` → Controller validates local result → `WorkflowService.register_task_handoff()` commits a separate transaction → `DispatchLedger.finish()` sets `DONE` and releases slot in another transaction. `run_once` attempts to mark `BLOCKED` on exception; abrupt process death may leave `ACTIVE`. The persisted dispatch key is `(task_id, round_number, input_sha)`; same-key automatic re-dispatch is suppressed. Neither ledger nor local handoff alone proves the current remote GitHub outcome.

## Uncertain-outcome matrix — evidence to reconcile (NOT a recovery command recipe)

**Universal safety rule:** Stop/pause new Controller dispatch before inspection. Preserve the `ACTIVE` or `BLOCKED` slot until an authorized operator has verified **owned container state, exact remote branch/PR SHA and local handoff/dispatch state**. A timeout is not evidence that no remote side effect occurred. Never automatically replay a potentially published job.

| Crash/uncertain boundary | Possible local observation | Remote effect that **may** already exist | Evidence / decision boundary |
| --- | --- | --- | --- |
| After `claim`, before or during Docker create | Dispatch still `ACTIVE` after abrupt death or `BLOCKED` if caught; slot=1 | No GitHub change expected this early, **but do not infer worker state from ledger alone** | Check exact dispatch ID/container names and task SHA, and reconcile any remaining worker before declaring stop; not an automatic retry |
| During Prepare, Code or Test, before Publish | `ACTIVE`/`BLOCKED`, no accepted `result.json` | Normally no publish yet; actual stage reached is uncertain after disconnect | Confirm stage/container lifecycle and artifacts; preserve slot on unknown status |
| During Git push or after push, before PR POST/PATCH | `ACTIVE`/`BLOCKED`; result missing | Branch may point to a new commit without a matching Draft PR | Verify remote head/ancestry and bot attribution independently; reconcile whether PR creation is safe **only through separately authorized tooling**, not a replay |
| After PR POST/PATCH, before `result.json` | `ACTIVE`/`BLOCKED`; result missing | Bot Draft PR and branch may already exist | Query remote branch/PR by exact repo/base/head/owner; do not trust an HTTP timeout as a failed request or repeat publish blindly |
| `result.json` present, before or during local handoff | Dispatch may still `ACTIVE`/`BLOCKED`, WFC task may remain `AWAITING_HANDOFF`/`CHANGES_REQUESTED` | Remote branch/PR may already be complete | Validate file provenance and exact SHA/PR against GitHub, then inspect WFC state; local result is worker-reported, not remote verification |
| After handoff commit, before dispatch `DONE` commit | WFC can be `READY_IN_REVIEW` while dispatch is still `ACTIVE`/`BLOCKED` | Remote branch/PR may already exist | Check **both** SQLite task/handoff and dispatch records plus exact GitHub SHA; no second publish solely because ledger says not DONE |

**What the built-in acknowledgement does and does NOT do:** The existing operator CLI `python -m my_wfc.controller CONFIG --acknowledge DISPATCH_ID --reconciled` (see [Phase 3.1 runbook](../phase-3.1.md)) requires an operator to assert prior reconciliation, calls `CoderRuntime.stop()` for exact-name owned containers, then `DispatchLedger.acknowledge()` releases the slot. **The `--reconciled` flag does not automatically inspect or prove GitHub remote/PR/handoff correctness.** Do not run this command from an agent or on production without separate Owner/operator authorization and recorded evidence. Acknowledgement **does not clear the original unique idempotency key** or automatically rerun the same job. No review round is consumed by an operational failure.

## TARGET contract (not CURRENT code)

The [merged CL-12 architecture ADR](../adr/CL-12-architecture.md) selects a separate unprivileged Linux-host Trusted Executor using local Rootless Podman CLI, with authenticated bounded Controller→Executor IPC, durable job identity, isolated Prepare/Code/Test/Publish stage containers and fail-closed unknown outcomes. [Executor security ADR §3](../adr/CL-12-executor-security.md) sketches `submit/status/stop/reconcile` and replay semantics. **These are design requirements, not implemented APIs.**

**Still open for a separately approved Executor Tech Spec / fault-injection work:** IPC transport and caller identity verification; exact durable-write checkpoints relative to container creation, Git push, PR creation and handoff; source of truth when Controller/Executor records disagree; authoritative publish reconciliation; operator `reconcile` privilege; crash/unknown-result states and retry policy. Require synthetic nonproduction tests for each side-effect boundary, without live credentials or reusing Phase 3.1 data.

## Read/analysis hygiene

1. Start at [documentation index](../INDEX.md) and this focused route, then inspect exact symbols, small line ranges and matching tests.
2. Check the current `main` SHA first; the source paths in this guide are navigation hints, **not** a claim that function behavior or deployment is permanently unchanged.
3. In a handoff, label each conclusion **SOURCE FACT / DESIGN DECISION / REPORTED TEST / UNKNOWN** and distinguish synthetic unit tests from real GitHub App, Docker, WSL2 or native Linux tests.
4. For any live operational action, STOP and use the Owner-authorized incident/recovery runbook and verified environment, not solely this document. Do not print credentials or raw sensitive worker output.
