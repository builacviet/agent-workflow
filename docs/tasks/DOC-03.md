# DOC-03 — Cross-model onboarding safety and recovery gap documentation

**Status:** Proposed, documentation-only, awaiting independent review and Owner merge. **Base:** DOC-02 Owner merge commit `2048ea9383509bf389aff6bdb2a3f02fb2e922ef` (2026-11-07). **Source:** Owner-supplied independent Claude read-only onboarding audit at that pinned commit, verdict **NEEDS IMPROVEMENT**, seven documentation findings (two High, one Medium-High, three Medium, one Low-Medium). This does not supersede DOC-01/DOC-02 approval or constitute approval to change runtime.

## Purpose

Improve a *fresh agent's* ability to distinguish what CURRENT code actually does, what is an untested edge case, and what requires separately authorized incident recovery or TARGET Executor design. Preserve unknowns and fail-closed conditions. Avoid pretending a documentation update has solved an engine ownership or distributed side-effect problem.

## Acceptance criteria

1. **AC1 — Post-acknowledgement continuation:** The [CURRENT incident guide](../guides/dispatch-recovery.md) distinguishes verified published+WFC handoff, published without handoff, verified no publish, and unknown/mismatched outcomes. Clarify that `ACKNOWLEDGED` releases only the global slot; the unique task/round/input SHA key remains spent. Do **not** invent a manual handoff/SQLite edit/forced retry or imply a working automated business-task repair path. An authorized operator records external evidence and obtains Owner approval for any remediation.
2. **AC2 — Container identity limits:** Enumerate the *actually observable* name, constant label, dispatch workspace/mount and creation metadata, and clearly state none uniquely proves container ownership or absence of late creation. If trusted evidence is insufficient, keep `ACTIVE`/`BLOCKED`, do not acknowledge, and escalate. True per-dispatch identity, immutable container ID persistence and checked cleanup require separately authorized code/security work.
3. **AC3 — Missing negative tests:** Explicitly list missing evidence for existing Draft PR PATCH, post-push abnormal PR status, lost POST/PATCH responses, Git push failure after remote change, revoke failure, operator CLI and real Docker stop/late create, and handoff-before-`DONE` crash. Tests mentioned as file references remain **source-present, not executed**, and GET-only tests do not prove POST/PATCH correctness.
4. **AC4 — Failure boundaries:** Add deterministic post-push rejection and post-`result.json` revoke/finalization uncertainty rows; document `WFC_RUNTIME_SECONDS` self-timeout as a bound, not proof of daemon stop or no late create.
5. **AC5 — CURRENT vs TARGET:** At points of claims, mark historical Phase 2.2 remote SHA verifier as superseded, state CURRENT Controller invokes host Docker CLI while TARGET forbids delegating generic engine API/socket and requires an authenticated Executor IPC. Distinguish existing four Docker phases from future Executor architecture.
6. **AC6 — ADR status correctness:** Update [CL-12.5 security ADR](../adr/CL-12-executor-security.md) header, historical PR gates and review footer with confirmed documentation merge status of PR #11/#12/#13, while **not** claiming parent CL-12 sign-off, implementation or production/security validation. Remove stale DOC-02 scope rationale from guide/INDEX.
7. **AC7 — Threat model terminology:** Replace ambiguous "Worker disconnect" onboarding route with Docker CLI/daemon or GitHub HTTPS request uncertainty; state CURRENT network restrictions (only Test has explicit `--network=none` among core phases), and that remote branch protection is **UNKNOWN**, not guaranteed by local Worker guard.
8. **AC8 — Integrity/retest:** Only changes to the exact seven Markdown paths below. Verify exact base/head, scope, Markdown relative links, source/test symbol references, and independently re-run *blind* onboarding with Claude or Codex at post-merge exact SHA. Report actual fetch/line counts only; token savings remain UNAVAILABLE without telemetry.

## Exact DOC-03 allowlist

- `AGENTS.md`
- `docs/INDEX.md`
- `docs/architecture.md`
- `docs/phase-3.1.md`
- `docs/guides/dispatch-recovery.md`
- `docs/adr/CL-12-executor-security.md`
- `docs/tasks/DOC-03.md` (this record)

## Scope and evidence contract

**Not allowed:** edits to Python/source/tests, GitHub workflow/CI, Docker/Podman/Compose or runtime configuration; touching live containers, DB, credentials, tunnel, workflows or other projects; invoking real recovery; changing already selected TARGET architecture; self-approving or merging. No real tests or fault injection are performed during this documentation task. Where the source lacks a safe operational answer, write **UNKNOWN / requires separate atomic Owner-approved task**, not a speculative runbook command.

**Independent review:** Fresh review must check seven Claude findings against source/test evidence at exact PR HEAD; maximum two rounds. Owner alone may merge. Any code hardening of dispatch ownership, metadata, reconciliation audit, PR idempotency and fault injection is separately scoped after onboarding sign-off.

**Baseline metrics (Claude, not directly comparable to Codex):** 22 distinct files opened, 24 file reads, 0 redundant reads; approximately 2,680 lines returned/exposed across tool families; 54 total tool calls, including access/setup/task bookkeeping; actual token telemetry **UNAVAILABLE**. Do not claim specific token savings from these figures.

## Independent Codex review round 1/2 — REQUEST_CHANGES

Reviewed PR #17 at exact HEAD `ff04694c9f30ee34934af4771331108320d8a793`, base `2048ea9383509bf389aff6bdb2a3f02fb2e922ef`; confirmed seven Markdown files and links. Verdict **REQUEST_CHANGES**: F2/F7 FIXED; F1/F3/F4/F5/F6 PARTIAL, plus four new documentation findings. This review did **not** run tests, Docker or production operations, and did **not** constitute independent approval.

Corrections in the same PR branch for round 2/2:

- **F1 / cleanup:** A valid exact-SHA `HANDOFF` event may have progressed beyond `READY_IN_REVIEW` into `IN_REVIEW` or later review/history while the dispatch slot remains held. Final `stop()` may fail after handoff but before `finish()`; this is not necessarily a handoff/finish-transaction failure. Require separate review-history and engine checks.
- **F3:** Distinguish unit/mock cleanup tests, the real-Docker *synthetic* `tests/live_phase31_smoke.py`, and the historical Coder-reported smoke pass. **No rerun at DOC-03 HEAD;** smoke does not prove container ownership, late create exclusion or specific self-timeout execution.
- **F4:** Matrix now includes `WFC_RUNTIME_SECONDS` self-timeout returning 124 through the entrypoint and generic phase-failure handling; Publish remote outcomes remain uncertain.
- **F5:** Clarify the Codex Code stage cannot commit/push, while trusted Worker Publish owns Git operations; manual authorized Coder workflows are distinct.
- **F6:** GitHub merge status of design ADRs does not, by itself, prove independent exact-SHA review records. Separate Owner task authorization **precedes coding**, mandatory synthetic negative tests happen **during implementation**, and evidence review/security gates precede acceptance/deployment.
- **Additional findings:** Correct CURRENT Auth0 opt-in vs default legacy Bearer and mode-dependent ping/info access; correct DOC-02 baseline terminology in recovery guide; cover final cleanup errors and distinguish engine failure from ledger transaction failure.

**Round 2 review required at the new exact PR HEAD.** The old Codex verdict does not apply automatically to later commits. Do not infer tested runtime behavior or measured token savings from these documentation edits. Owner alone may merge.
