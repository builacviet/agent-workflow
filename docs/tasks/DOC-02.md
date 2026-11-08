# DOC-02 — Onboarding follow-up: CURRENT recovery navigation and doc consistency

**Status:** Proposed documentation-only follow-up to Owner-merged DOC-01 ([PR #15](https://github.com/builacviet/agent-workflow/pull/15)). Motivated by the Owner-supplied independent Codex onboarding audit (NEEDS IMPROVEMENT), which read 25 files/51 successful fetches and reported 1,608 lines shown vs 5,507 returned. **Actual token usage is unavailable**, so do not invent token savings.

## Purpose and constraints

Fix *navigation* and *dated documentation accuracy* without changing behavior, source, tests or the previously accepted CL-12 architectural decisions. Keep the historical Phase 3.1 contract intact, while clearly marking superseded sections and routing new agents straight to current source/test symbols.

## Acceptance criteria

1. **AC1 — Scenario route:** `docs/INDEX.md` prioritizes the current Phase 3.1 runbook and links a focused timeout/disconnect source/test map; agent can locate claim, lifecycle, publish, handoff, ledger and relevant tests without guessing file paths.
2. **AC2 — CURRENT recovery evidence:** Add `docs/guides/dispatch-recovery.md` with source-verified uncertain-effect matrix and blocked operator gates. Update Phase 3.1 runbook with an entry link. Do **not** add automatic retry, new CLI permission or unverified production procedures.
3. **AC3 — Superseded markers:** Mark `docs/my-wfc.md` historical for deprecated remote-verifier/dispatch descriptions; mark `docs/tasks/PHASE-3.1.md` original AC/design as dated, superseded by its later Owner revisions; preserve historical evidence and annotate stale claims in place without silently deleting their audit trail. Current runbook supersedes them for current responsibilities.
4. **AC4 — ADR status:** Correct CL-12.7 and CL-12.6 ADR status text *only* to reflect confirmed Owner-merged documentation PRs #13/#12, while keeping **parent CL-12 final sign-off, Executor implementation and production readiness separate/unverified**.
5. **AC5 — Open decisions:** Maintain explicit checklist for TARGET IPC/durable checkpoints/reconciliation as **future Executor Tech Spec** work, not unreviewed protocol detail.
6. **AC6 — Scope/test integrity:** Only Markdown paths `docs/INDEX.md`, `docs/guides/dispatch-recovery.md`, `docs/tasks/DOC-02.md`, `docs/phase-3.1.md`, `docs/my-wfc.md`, `docs/tasks/PHASE-3.1.md`, `docs/adr/CL-12-architecture.md`, `docs/adr/CL-12-deployment.md`. Verify links, source symbols and HEAD/changed-path list. No source/runtime/DB/Compose/CI/host/config edits. No merge without independent Owner review.
7. **AC7 — Retest:** A fresh agent with no conversation history repeats the timeout scenario using `AGENTS.md` and `docs/INDEX.md`. Record actual files and fetches/lines (tokens only if tool exposes them). No promised percentage reduction until measured; report remaining correctness gaps.

This is documentation navigation, not authorization for CL-07, CL-11, Executor/GHCR/Grafana implementation or production cutover.


## Independent Codex review — REQUEST_CHANGES (2026-11-07)

[PR #16](https://github.com/builacviet/agent-workflow/pull/16) was independently reviewed at exact HEAD `4b2aad71bd93df44ffea7b457f970d38fb83b702`. Result: **REQUEST_CHANGES**, seven findings (one High, four Medium, two Low). DOC-02 follow-up changes remain within the same 8-file Markdown allowlist: (1) clarify pre-acknowledge verification of correct DB/dispatch lease, Docker endpoint, container provenance and possible late create; (2) distinguish existing-Draft-PR head/body drift and POST/PATCH response loss; (3) correct post-merge ADR statements; (4) link runbook to recovery guide and distinguish ACTIVE/BLOCKED; (5) locally label unsafe/historical WFC instructions; (6) add focused stage/publish tests and distinguish mock/real-bare-Git/HTTP GET coverage; (7) fix literal-escaped newlines in Phase 3.1 task banner and mark original AC.

**Out-of-scope dated paragraph:** Security ADR §8 still calls PR #12/#13 Draft. This historical claim is flagged in `docs/INDEX.md` and `docs/guides/dispatch-recovery.md`; that ADR is not changed without a separately authorized path allowance. No source/code/runtime behavior changes, no new tests run, no measured token savings asserted. **Independent re-review required on the updated exact HEAD before Owner merge.**