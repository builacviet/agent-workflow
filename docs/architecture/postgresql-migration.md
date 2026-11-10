# PostgreSQL target: staged migration (no cutover authorization)

**Status:** implementation foundation on a separate Draft PR. Existing SQLite
database, running containers, Auth0 and PR #37 remain untouched.

## Scope and deployment sequencing

1. Operator provisions a local-only PostgreSQL admin password in
   `.secret/postgres-admin-password` (Git-ignored). Never paste the value
   into prompts, GitHub, Compose environment or diagnostics.
2. Optional isolated stack:
   `docker compose -f compose.postgres.yaml --profile database up -d postgres`.
   This uses loopback-only port 15432 for the trusted host Controllers;
   containers on the same Compose network can use port 5432 by service name
   only after the application is migrated.
3. First initialization executes `000_roles.sql`, `001_schema.sql`,
   `002_grants.sql` inside the **new** PostgreSQL volume. Roles are NOLOGIN
   initially. The operator must provision a distinct strong SCRAM password and
   enable LOGIN for each required service using interactive `psql` commands,
   then store DSNs in ignored per-service runtime secrets. Do not enable
   a service before its effective grants have been tested.
4. PostgreSQL adapters must be built and tested on an isolated DB for workflow,
   dispatch, CI, and webhook. PostgreSQL's row locking and UNIQUE constraints
   replace SQLite BEGIN IMMEDIATE; the process-level role lock becomes a
   session advisory lock held independently for the Dev Controller and CI
   Watcher. The advisory lock module exists but is not yet wired.
5. Migrate data only during an Owner-approved write-quiesced cutover with a
   consistent SQLite backup; verify row counts, identities, foreign keys,
   token-independent CI provenance, ordering, and active dispatch statuses.
   Never dual-write or auto-switch back after new PostgreSQL writes.
6. Run real PostgreSQL integration tests, privilege-denial tests, concurrent
   Controller tests and full webhook->CI->review gate E2E. Owner must approve
   the separate runtime cutover; no production activation from this PR.

## Database access matrix (initial grants)

| Role | Tables/operations |
| --- | --- |
| `mcp_user` | SELECT/INSERT/UPDATE core workflow/review tables; SELECT CI status |
| `webhook_user` | SELECT/INSERT webhook inbox only |
| `ci_user` | SELECT task/inbox, SELECT/INSERT/UPDATE CI task ledger, append CI evidence/processed |
| `controller_user` | SELECT/INSERT/UPDATE workflow and dispatch ledgers; SELECT CI status |
| `db_migrator` | Owns schema; runtime applications never get migration DDL |
| Coder Worker, router, ngrok | No database role |

These SQL grants are **provisional** until every adapter's actual SQL is
audited and verified against real PostgreSQL. Grant changes require a schema
migration/review. Service roles have NOLOGIN until the operator enables them;
the bootstrap grants alone are not operational credentials.

## Important constraints

- PG target DDL must be checked against existing SQLite migration variants,
  including `controller_ci_provenance`, before transferring data.
- No changes to old SQLite tables or binary files in this foundation.
- `postgres:17-bookworm` is a development major-version reference; pin the
  reviewed immutable image digest before production.
- `CREATE TABLE IF NOT EXISTS` is only for an empty bootstrap volume, not
  evidence that an arbitrary existing schema is compatible. Production must
  use a versioned, validated migration runner.
