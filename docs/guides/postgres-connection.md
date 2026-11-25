# PostgreSQL runtime connections — current R2 boundary

**Source baseline:** merged main after [#139](https://github.com/builacviet/agent-workflow/pull/139), `5a569da13ffe5bd2fc158d2e82c8f41a704d67ec`, plus DB-06D1 factory retirement. PostgreSQL-only runtime persistence; no verified production activation. Read [canonical DB rules](../conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership) for selection and transaction ownership.

## Configuration and readiness

`PG_RUNTIME_DSN` must be a `postgresql+psycopg://user:password@host:port/database` TCP URL with a user, password and database. Percent-encode reserved characters in credentials. URL query/fragment, ambiguous hosts and libpq host overrides for local provisioning are rejected. Never log, commit or share the DSN. Use separate least-privilege runtime credentials and privileged `PG_MIGRATOR_DSN` migration credentials.

`agent_workflow.infrastructure.database.session_factory.PostgresFactory.open(AppConfig.from_env())` explicitly creates a factory; importing the package never connects. The local `agent_workflow.admin` commands use `AppConfig.for_local_task_authorization()` and **require a loopback host** (`127.0.0.1` or `::1`). The factory checks the reviewed Alembic head **`pg02d_webhook_digest`** on opening (see `EXPECTED_REVISION` in source); schema missing, empty or mismatched fails closed with a sanitized persistence exception. No implicit DDL, auto migration or SQLite fallback.

Use `get_scoped_session()` for Readers and independent Repository writes; use the existing `SqlAlchemyUnitOfWork` for coordinated business transactions and repository-owned workflow write scopes. `commit()` records intent; workflow context exit checks deadlines, commits the Session, then commits the outer connection. Review context retains its read-only snapshot and rolls back on exit. Independent CI/webhook transactions retain the fixed short profile. `get_sync_session(connection)` lends a Session on an existing connection; `connect_session()` and `transaction()` serve infrastructure-owned connection scopes (including leadership and database fixtures). The two legacy auto-commit Session wrappers are retired. Preserve lock/isolation/timeout/durability and pinned leadership. Connections close after use; bounded connect timeout and sanitized errors remain.

## Local CLI contract

From an installed checkout, with an **already migrated and explicitly authorized loopback database**:

```powershell
python -m agent_workflow.admin --help
# The following require a safe, configured PG_RUNTIME_DSN:
python -m agent_workflow.admin provision --file .\authorized-task-spec.json
python -m agent_workflow.admin seed-synthetic
```

`provision`, `seed-history`, `seed-synthetic` and `reset-synthetic` are PostgreSQL-only. The legacy `--postgres` argument is accepted but does **nothing**; the help describes it as a deprecated no-op. Only run fixture seeding/reset against an explicitly disposable/approved database. For expected operator validation/business/corrupt-data errors the CLI exits `2`; temporary PostgreSQL failures or timeouts exit `75`. CLI emits bounded JSON lines on stderr with validated `CORRELATION_ID` and adds a safe correlation comment to SQL statements when a current ID exists. Unrecognized or unsafe IDs are regenerated at the CLI boundary.

## Verification (disposable PostgreSQL 16 only)

Use the [Alembic runbook](postgres-alembic.md) to migrate an **empty disposable PostgreSQL 16 database** with `PG_MIGRATOR_DSN` before running integration tests. Set `PG_TEST_DSN` to that database only; never aim tests at production/shared or existing workflow data.

```powershell
python -m pytest -q tests/test_pg_connection.py
python -m pytest -q tests/integration/postgres/test_task_authorization_live.py
```

Without `PG_TEST_DSN`, opt-in live tests may skip; a green regular pytest run does **not** prove they executed. The administrative seed/reset live integration additionally requires `R2_CLI_DISPOSABLE=1` and an explicitly empty disposable database.

**Deployment boundary:** the tracked root `compose.yaml` still contains legacy SQLite state mounts, while source `mcp/server.py` composes a PostgreSQL provider. Neither this guide nor a passing migration test proves the root Compose stack is currently a valid end-to-end PostgreSQL deployment.
