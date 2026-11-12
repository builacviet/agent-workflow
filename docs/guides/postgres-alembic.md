# PostgreSQL + Alembic: V1-PG-01 foundation

**Stage:** fresh schema baseline and disposable-PostgreSQL CI only. No PostgreSQL runtime wiring, no SQLite data import, no new Docker or Worker privileges, no production cutover.

## Why a new implementation?

Legacy Draft PR #39 was closed without merge. This branch starts from main after PR #47. The Owner-supplied migration archive contains a Flask-Migrate/Alembic example. Its revisioned upgrade/downgrade pattern is useful, but the Flask runtime and its unrelated business tables are not used.

PostgreSQL schema changes are managed exclusively by **standalone Alembic**. The existing SQLite `schema_migrations` registry is retained only for the existing SQLite adapters until a separately approved cutover; it is not copied to PostgreSQL. The baseline is revision `pg01_baseline` and fails on an unknown pre-existing `wfc` schema rather than stamping it.

## Operator setup on an EMPTY PostgreSQL database

Install the optional dependencies:

```powershell
python -m pip install -e ".[postgres]"
$env:WFC_PG_MIGRATOR_DSN = "postgresql+psycopg://MIGRATOR_USER:REDACTED@127.0.0.1:5432/workflow"
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini current
```

Use a privileged migration-only account; do not store DSNs, passwords or secrets in the repository. The `WFC_PG_MIGRATOR_DSN` environment variable is required and accepts only a psycopg-backed PostgreSQL URL. Runtime database roles must not receive DDL permissions.

The baseline creates the dedicated `wfc` namespace and workflow/review, dispatch, runtime evidence, webhook, CI and future publication-intent tables. Alembic tracks schema revision in `public.alembic_version`. The publication-intent table is **not** an activated publish feature: it is reserved for a future reviewed PostgreSQL adapter replacing the SQLite store in Draft PR #48.

The migration runs transactional DDL under a nonblocking **transaction advisory lock for concurrent migrators**. It does NOT protect against concurrent Controllers; that requires separate session ownership, row locks, transaction boundaries and fault injection in PG-03. Keep all Controllers stopped for any authorized schema or data migration.

## Never downgrade this stateful baseline

`alembic downgrade` intentionally fails rather than delete historical dispatch/CI/review evidence. Rollback requires an operator-authorized verified database restore, snapshot integrity checks and independent inspection of any live dispatch and GitHub effects before allowing new writes.

## Follow-on dependency gates

1. **PG-02**: PostgreSQL adapters for Workflow, Dispatch, Controller CI and Webhook with explicit ports/UoW, least-privilege DB roles, connection lifecycle and PG publication-intent store. Preserve rollback, idempotency, replay and evidence semantics.
2. **PG-03**: Controller/CI session advisory lock ownership plus fenced row-level claims, crash and concurrent-Controller tests. Never assume the schema migration lock alone solves dispatch concurrency.
3. **PG-04**: stopped-writer SQLite backup; preserve original sandbox fences; deterministic data copy, per-table counts/checksums, referential-integrity verification, sequence restoration, restart/restore drill and live end-to-end with synthetic faults. Only the Owner can authorize the production cutover.

GitHub Actions runs a disposable PostgreSQL 16 instance with synthetic CI credentials. All other production services continue using their original database until the full cutover gates are met.
