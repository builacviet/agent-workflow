# PG02-B — Workflow/Review PostgreSQL adapter: implementation contract (preparation)

**Status:** TECH LEAD PREPARATION — docs-only; **not** approved PG02-B code execution, a completed adapter, or proof of PostgreSQL runtime cutover. Task: [PG02-B on Notion](https://app.notion.com/p/416f92014abd818cb9deeb1522729b1b).

**Baseline inspected:** `main@bbb17d22b5d1dff0b4378c1627551c3c8b09b3b3` after PG02-0 merge. [PG02-A PR #52](https://github.com/builacviet/agent-workflow/pull/52) provides an opt-in connection factory, but is **not** considered merged here. Reconcile its **merged** API and latest `main` before approving PG02-B code paths, base SHA and tests.

This is the **manual DEVELOPMENT** flow: ChatGPT TL task/architecture → Codex Dev/self-review → Draft PR → exact-HEAD CI → ChatGPT TL review → Owner merge. Do not insert the TARGET product's QC/MTP, immutable-package or independent-reviewer gates.

## Goal and boundaries

Implement the `WorkflowRepository` / `WorkflowUnit` port for workflow tasks, review claims, reviews, findings and task events, backed by the **existing** PostgreSQL Alembic `pg01_baseline` schema. Reuse PG02-A's validated connection/transaction factory instead of creating a second engine, DSN parser, transaction manager or revision gate.

**Source of truth for behavior:** `src/agent_workflow/storage/repository.py` (protocol + `SQLiteUnit` and its transaction/read semantics), `src/agent_workflow/domain/service.py` (use-case/state-machine validation), `alembic/versions/pg01_baseline.py` (PostgreSQL schema), current characterization tests, and the accepted Notion PG02-B task. These take precedence over this preparatory note if they have changed.

**Likely owned files, to confirm after PG02-A merge:**

- NEW `src/agent_workflow/storage/postgres_workflow.py` — SQLAlchemy PostgreSQL adapter implementing existing repository/unit contract.
- EXISTING `src/agent_workflow/storage/repository.py` — change **only** if essential for protocol compatibility; retain SQLite paths and service behavior.
- NEW `tests/test_pg_workflow_adapter.py` — parity, negative, rollback, live PostgreSQL tests.
- `.github/workflows/v1-ci.yml` — add tests to disposable PG CI **only if needed**; no removal of existing checks.
- This architecture contract and task-scoped docs may be updated with verified implementation details.

**Forbidden without separate Owner-approved scope:** Controller/CI watcher/MCP/Webhook/Worker/Sandbox runtime wiring; `compose.yaml`, connection secrets, other worktrees; Alembic/schema/DDL edits; SQLite data import/migration; changing the authoritative domain state machine; auto-switching services or authorizing a merge.

## Port method families (not a new abstraction)

| Contract family | Existing `WorkflowUnit` methods | PostgreSQL tables / semantic obligations |
| --- | --- | --- |
| Task authorization / status | `insert_task`, `find_task`, `find_authorization`, `set_status`, `set_head`, `list_ready_tasks`, `list_claimed_tasks`, `set_rounds` | `tasks`; preserve dictionary keys, metadata as JSON **text**, status and two-round checks, sorted task IDs and exact SHA. |
| Review claims | `insert_claim`, `find_active_claim`, `find_claim`, `expire_claim`, `submit_claim` | `review_attempts`; one active claim per task, lease and submission mutations atomic, duplicate claim failure never partially commits. |
| Reviews and findings | `insert_review`, `list_reviews`, `insert_finding`, `list_findings` | `reviews`, `review_findings`; preserve round ordering, finding IDs and uniqueness, FK integrity, `None` and scalar types. |
| Event/handoff history | `insert_event`, `list_events`, `find_head_handoff` | `task_events`; ordered event IDs, immutable reviewed/head SHA provenance, event metadata as JSON **text**. |
| CI eligibility used by review | `ci_approved`, `advance_failed_ci_head` | Read/update `controller_ci_tasks` and `controller_ci_observations` only as required to satisfy existing `WorkflowUnit` call sites. Dedicated **CI ledger** ownership remains PG02-E; coordinate this seam before writing SQL. |
| Legacy test/migration helpers | `import_historical_head`, `delete_synthetic_history` | SQLite-only historical fixtures/cleanup. **Unresolved:** locate actual callers and explicitly decide if PostgreSQL must implement guarded equivalent or safely reject; never silently port destructive history deletion. |

`WorkflowRepository.transaction()` and `.read()` must return a `WorkflowUnit` context usable by the unmodified `WorkflowService`. Read results must be ordinary `dict` with current column labels, ordering, `None`, numeric/boolean conventions and JSON text semantics; do not leak SQLAlchemy `Row` or `RowMapping` objects into the domain.

## Acceptance / test matrix for the future code PR

| ID | Required behavior | Evidence to prepare |
| --- | --- | --- |
| B-01 Contract parity | Existing service calls and returned dictionaries match SQLite characterization paths, including status/round/head rules. | Compare representative WorkflowService flows against SQLite and disposable PostgreSQL adapters; don't normalize away incompatible values. |
| B-02 Atomic writes | Successful `transaction()` commits; any exception, uniqueness/FK violation or failed review submission rolls back **all** changes. | Insert task+event in one unit; induce error after first write; verify neither survives; check duplicate claim / review conflicts. |
| B-03 Review safety | Concurrent claims obey `one_active_claim`; stale SHA, token/lease mismatch, round uniqueness and duplicate submission do not bypass domain rules. | Two independent PG connections, same-task competing claims and replay, verified state/row counts; no production DB. |
| B-04 Read/history fidelity | Task, event, review, finding and handoff ordering/types/JSON shape match existing callers. | Golden characterization fixtures, sorted event/review queries, nullable fields and metadata serialization. |
| B-05 CI authorization boundary | `ci_approved` stays fail-closed on missing/stale/invalid evidence; CI-repair HEAD mutation follows preexisting constraints. | Negative tests using missing/stale `controller_ci_tasks`/observations; no automatic CI pass or reviewer authorization. |
| B-06 DB lifecycle | Reuse PG02-A migration-version guard, bounded connection and resource cleanup; no schema creation/migration on adapter startup. | PG_TEST_DSN disposable migrated CI job; bad revision/connection and teardown/rollback tests. |
| B-07 Scope & delivery | No service switch, database import, new Worker credentials or new Alembic tables; exact-HEAD CI and Dev/TL checklists. | Git diff allowlist, architecture gate, full suite, live PG tests, Codex self-review and TL SHA-bound decision. |

Implementation notes: parameterize SQL (SQLAlchemy named binds; never interpolate caller-controlled identifiers); preserve intentional uniqueness/FK error mapping to the public `WorkflowError` contract only where existing callers require it; make transactional isolation/concurrency guarantees explicit rather than inferring them from passing single-connection tests. Do not duplicate domain state transitions in PostgreSQL adapter.

## Pre-code resolution checkpoints (not new manual gates)

1. **PG02-A must be Owner-merged.** Check its actual factory API and approved tests on `main`; then pin PG02-B base SHA, worktree and exact changed-path allowlist.
2. Resolve `WorkflowUnit` CI-method ownership versus PG02-E and legacy-only helper compatibility above, using concrete call sites. Only escalate a real conflict to Owner.
3. Prefer a single focused Dev PR; report AC coverage and `PASS / FAIL / N/A / NOT VERIFIED` evidence; no automatic merge.

**This document is implementation preparation only.** It can be reviewed and merged independently, but **does not** unblock PG02-B implementation until its dependency and API contract have been verified.
