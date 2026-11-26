# PostgreSQL runtime connections — current R2 boundary

**Source baseline:** merged main after [#150](https://github.com/builacviet/agent-workflow/pull/150), `fb552670bf2b9985e52c862482f3af613d3263a6`. PostgreSQL-only runtime persistence; no verified production activation. Read [canonical DB rules](../conventions/engineering-standards.md#postgresql-session-query-and-transaction-ownership) for selection and transaction ownership.

## Configuration and readiness

`PG_RUNTIME_DSN` must be a `postgresql+psycopg://user:password@host:port/database` TCP URL with a user, password and database. Percent-encode reserved characters in credentials. URL query/fragment, ambiguous hosts and libpq host overrides for local provisioning are rejected. Never log, commit or share the DSN. Use separate least-privilege runtime credentials and privileged `PG_MIGRATOR_DSN` migration credentials.

`agent_workflow.infrastructure.database.session_factory.PostgresFactory.open(AppConfig.from_env())` explicitly creates a factory; importing the package never connects. The local `agent_workflow.interfaces.cli.admin` commands use `AppConfig.for_local_task_authorization()` and **require a loopback host** (`127.0.0.1` or `::1`). The factory checks the reviewed Alembic head **`pg02d_webhook_digest`** on opening (see `EXPECTED_REVISION` in source); schema missing, empty or mismatched fails closed with a sanitized persistence exception. No implicit DDL, auto migration or SQLite fallback.

## Session and transaction ownership

- `session_factory.py`: `get_scoped_session()` lends an idle connection-bound Session; `get_sync_session(connection)` lends a Session on an existing connection. `connect_session()` checks out a connection, including for pinned leadership. The raw `transaction()` connection helper has disposable database test consumers; it is not a business transaction API. The two legacy auto-commit Session wrappers are retired.
- `unit_of_work.py`: `SqlAlchemyUnitOfWork` coordinates one transaction. Application owns business commit intent; an independent repository may reuse the concrete implementation locally (dispatch writes). `commit()` records intent; workflow context exit refreshes timeout, commits the Session, checks the deadline, closes the Session and physically commits the outer transaction. Read-only or uncommitted scopes roll back. CI writes retain their fixed profile, not the dispatch budget.
- `transaction_options.py`: shared PostgreSQL settings only; no transaction lifecycle. CI reads use READ COMMITTED READ ONLY; CI/webhook writes use READ COMMITTED with fixed 5s lock/statement timeouts and synchronous commit. Review context uses REPEATABLE READ READ ONLY; dispatch pre-launch reads use READ COMMITTED READ ONLY with the dynamic workflow timeout. Workflow timeout is capped at 30 seconds and bounded by remaining budget, with a minimum of 1ms to avoid PostgreSQL's unlimited zero setting.
- `lock_keys.py`: stable transaction advisory namespace and domains for migration/dispatch, and separate task-authorization hash seed. Keep values stable. Session leadership retains its distinct session lock namespace and pinned connection; Alembic owns its standalone migration engine.

Connection timeout (5s) belongs to the factory; migration connect/lock timeouts (5s each) belong to Alembic; leadership statement timeout (5s) belongs to its adapter. These named constants have independent meanings. Preserve transaction-local settings, deadlines, lock ordering, durability, connection cleanup and sanitized failures.

## Local CLI contract

From an installed checkout, with an **already migrated and explicitly authorized loopback database**:

```powershell
python -m agent_workflow.interfaces.cli.admin --help
# The following require a safe, configured PG_RUNTIME_DSN:
python -m agent_workflow.interfaces.cli.admin provision --file .\authorized-task-spec.json
python -m agent_workflow.interfaces.cli.admin seed-synthetic
```

`provision`, `seed-history`, `seed-synthetic` and `reset-synthetic` are PostgreSQL-only. The legacy `--postgres` argument is accepted but does **nothing**; the help describes it as a deprecated no-op. Only run fixture seeding/reset against an explicitly disposable/approved database. For expected operator validation/business/corrupt-data errors the CLI exits `2`; temporary PostgreSQL failures or timeouts exit `75`. CLI emits bounded JSON lines on stderr with validated `CORRELATION_ID` and adds a safe correlation comment to SQL statements when a current ID exists. Unrecognized or unsafe IDs are regenerated at the CLI boundary.

## Verification (disposable PostgreSQL 16 only)

Use the [Alembic runbook](postgres-alembic.md) to migrate an **empty disposable PostgreSQL 16 database** with `PG_MIGRATOR_DSN` before running integration tests. Set `PG_TEST_DSN` to that database only; never aim tests at production/shared or existing workflow data.

```powershell
python -m pytest -q tests/test_pg_connection.py
python -m pytest -q tests/integration/postgres/test_task_authorization_live.py
```

Without `PG_TEST_DSN`, opt-in live tests may skip; a green regular pytest run does **not** prove they executed. The administrative seed/reset live integration additionally requires `R2_CLI_DISPOSABLE=1` and an explicitly empty disposable database.

**Deployment boundary:** the tracked root `compose.yaml` still contains legacy SQLite state mounts, while the canonical Presentation entrypoint `python -m agent_workflow.interfaces.mcp.server` registers scoped providers from `composition/mcp_deps.py`; `composition/postgres_mcp.py` owns the PostgreSQL dependency scopes. The legacy MCP namespace and Bootstrap MCP provider module are retired. Neither this guide nor a passing migration test proves the root Compose stack is currently a valid end-to-end PostgreSQL deployment.
