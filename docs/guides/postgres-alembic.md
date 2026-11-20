# PostgreSQL + Alembic — current operator boundary and historical V1-PG-01 design

**Current verified source (2026-11-20):** `main@70b333be08d87a90190cdb06cdde0a9b1cb635f3` after merged PR #98. **Required PostgreSQL head for `PostgresFactory`: `pg02d_webhook_digest`**, not `pg01_baseline` or `pg02c_agent_dispatch`. Local admin and feature-specific Task/Review adapters are now PostgreSQL-first; **this does not mean root Compose or production E2E has been cut over**. The earlier PG01/PG02 preparation narrative below is preserved as historical design evidence, not an instruction to regress to older migrations.

## Why a new implementation?

Legacy Draft PR #39 was closed without merge. This branch starts from main after PR #47. The Owner-supplied migration archive contains a Flask-Migrate/Alembic example. Its revisioned upgrade/downgrade pattern is useful, but the Flask runtime and its unrelated business tables are not used.

PostgreSQL schema changes are managed exclusively by **standalone Alembic**. The existing SQLite `schema_migrations` registry is retained only for the existing SQLite adapters until a separately approved cutover; it is not copied to PostgreSQL. The original baseline is revision `pg01_baseline` and requires a clean `public` schema rather than adopting unknown tables. The forward `pg02c_agent_dispatch` migration renames PostgreSQL Dev dispatch and evidence tables; `pg02d_webhook_digest` then enforces webhook inbox body-digest uniqueness. The runtime factory expects the **current exact head** `pg02d_webhook_digest` on this verified source baseline. Do not pin deployment/test setup to an earlier revision.

## Operator setup on an EMPTY PostgreSQL database

Install the optional dependencies:

```powershell
python -m pip install -e ".[postgres]"
$env:PG_MIGRATOR_DSN = "postgresql+psycopg://MIGRATOR_USER:REDACTED@127.0.0.1:5432/workflow"
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini current
```

Use a privileged migration-only account; do not store DSNs, passwords or secrets in the repository. The `PG_MIGRATOR_DSN` environment variable is required and accepts only a psycopg-backed PostgreSQL URL. Runtime database roles must not receive DDL permissions.

The migrations create tables in PostgreSQL's default `public` schema for workflow/review, dispatch, runtime evidence, webhook, CI and publication-intent foundations. Alembic tracks revisions in `public.alembic_version`. Controller/worker PostgreSQL composition and a PostgreSQL-only local admin CLI now exist in source; **do not treat that as verified production activation**. The publication-intent table is **not** proof of an activated two-phase publish/reconciliation feature; the old SQLite-oriented Draft PR #48 was closed without merge and its plan is historical.

The migration runs transactional DDL under a nonblocking **transaction advisory lock for concurrent migrators**. It does NOT protect against concurrent Controllers; that requires separate session ownership, row locks, transaction boundaries and fault injection in PG-03. Keep all Controllers stopped for any authorized schema or data migration.

## Never downgrade this stateful baseline

`alembic downgrade` intentionally fails rather than delete historical dispatch/CI/review evidence. Rollback requires an operator-authorized verified database restore, snapshot integrity checks and independent inspection of any live dispatch and GitHub effects before allowing new writes.

## Historical PG01 follow-on dependency gates (not a current execution order)

1. **PG-02**: PostgreSQL adapters for Workflow, Dispatch, Controller CI and Webhook with explicit ports/UoW, least-privilege DB roles, connection lifecycle and PG publication-intent store. Preserve rollback, idempotency, replay and evidence semantics.
2. **PG-03**: Controller/CI session advisory lock ownership plus fenced row-level claims, crash and concurrent-Controller tests. Never assume the schema migration lock alone solves dispatch concurrency.
3. **PG-04**: stopped-writer SQLite backup; preserve original sandbox fences; deterministic data copy, per-table counts/checksums, referential-integrity verification, sequence restoration, restart/restore drill and live end-to-end with synthetic faults. Only the Owner can authorize the production cutover.

GitHub Actions runs disposable PostgreSQL 16 migration and development-Compose checks. These checks are not proof of live production service configuration. Before a new operator migration, independently verify the actual `PG_MIGRATOR_DSN` target, expected schema revision, running writers and an approved restore plan; never use a shared/production database for tests.

## Current verification limits

`python -m alembic -c alembic.ini upgrade head` is the supported reviewed migration command for an authorized **disposable PostgreSQL database**. `PostgresFactory.open(...)` checks revision but does not migrate. The separate **R2-QG-01** target for meaningful `alembic check` requires wiring canonical `Base.metadata` to the Alembic environment and proving ORM schema drift detection; at this baseline the proposed full quality gate is **not yet enforced**. Never claim schema sync from a zero exit code when `target_metadata=None`.

See [current runtime connection guide](postgres-connection.md) and [as-built architecture](../architecture.md) rather than treating old PG01 status paragraphs as today's runtime facts.
