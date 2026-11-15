# Dev completion checklist — DB-STD-02

`docs/templates/dev-task-evidence.md` is referenced by the Notion task but is absent from the approved main base. This checklist follows the existing `docs/evidence/DB-STD-01-dev-checklist.md` instead.

## Identity and scope

- Task: [DB-STD-02](https://app.notion.com/p/417f92014abd8132ae18cda7ce1c6a93)
- Approved BASE: `8ffe2c31e1af5470836c8579bd180489d2c0cfe7`
- Worktree / branch: `.worktrees/db-std-02` / `codex/db-std-02`
- TL agreement for the conditional factory path: task comment `419f9201-4abd-800d-9eef-001d2188b61c` ("anh cho phép sửa postgres_connection").
- Changed paths: `postgres_connection.py`, `postgres_workflow.py`, `postgres_dispatch.py`, their two allowlisted adapter test files, and this checklist. All are on the task's exact allowlist.

## Checked items

| Item | Verdict | Evidence |
| --- | --- | --- |
| UoW atomicity and rollback | PASS locally; PostgreSQL 16 pending | Factory owns one Session inside one outer transaction. Its post-Session-commit deadline hook runs before the outer commit, so a failure rolls back. The SQLite lifecycle regression passes; live Workflow and Dispatch deadline tests are added for CI. |
| Read-only isolation | NOT VERIFIED live | Factory sets `REPEATABLE READ READ ONLY` before Session work. A PostgreSQL test checks the setting, stable snapshot, and rejected write. Local run skips it without `PG_TEST_DSN`. |
| Locks and ordering | NOT VERIFIED live | Existing `FOR UPDATE` and `pg_advisory_xact_lock` statements and call ordering were inspected and retained. Existing concurrent claim and lock-wait tests remain in the PostgreSQL CI job. |
| Deadline and error behavior | PASS locally; PostgreSQL 16 pending | Adapter checks still run before Session commit; factory runs the second check after Session commit and before outer commit. New live tests inject failure at either check and verify rollback and slot release. |
| Compatibility and architecture | PASS locally | `repository_session` is unchanged; the new factory context is additive. No schema, migration, wiring, policy, or leadership changes. Architecture policy tests pass. |
| Security and external effects | PASS by inspection | Existing bound timeout values, database errors, and transaction lock lifetimes are retained. Tests use disposable fixtures; no production database or migration was touched. |
| Scope and diff review | PASS | `git diff --check` passes. All six changed paths are allowlisted. The unrelated modification in the main worktree was untouched. |

## Verification

| Command / environment | Result | Limit |
| --- | --- | --- |
| Ruff check and Ruff format check on five changed Python paths; Windows Python 3.12.10 | exit 0 / exit 0 | None |
| Black check on five changed Python paths; Windows | exit 0 | None |
| Focused pytest: Workflow, Dispatch, connection, architecture; Windows | 33 passed, 28 skipped | Live PostgreSQL tests skipped because `PG_TEST_DSN` is unavailable locally. |
| Full pytest with temp directory outside the repository; Windows | 623 passed, 65 skipped, 45 subtests passed | Live PostgreSQL and other opt-in tests skipped. |
| PostgreSQL 16 CI on exact PR HEAD | NOT VERIFIED | To be recorded in the Draft PR after publication. |

The first full-suite attempts placed pytest's temporary secret fixtures under the repository; the security reader deliberately rejected paths below `.git`. Re-running with a temporary directory outside the repository passed. No product code or tests were changed to suppress this check.
