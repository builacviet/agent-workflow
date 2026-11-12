# PostgreSQL runtime connections

PostgreSQL runtime access is opt-in and is not wired into existing services. Set
`PG_RUNTIME_DSN` to a `postgresql+psycopg://user:password@host:port/database`
URL with percent-encoded credentials. An explicit TCP host, user, password and
database are required; query parameters and fragments are rejected. Never log
or share the URL. Supply runtime credentials separately from the privileged
`PG_MIGRATOR_DSN`; configure least-privilege database roles independently.

Call `PostgresFactory.open(AppConfig.from_env())` explicitly, use its
`transaction()` context for short units of work, and close the factory (or use
it as a context manager). A successful transaction commits; an exception rolls
it back. Connections close after each use and connect timeout is five seconds.
Opening verifies exactly the reviewed `pg01_baseline` Alembic revision and fails
closed on connection errors or missing, empty, or mismatched revision. It never
runs migrations. Apply reviewed migrations separately with migration credentials.

For manual verification, use only a disposable database already migrated to
the baseline, set `PG_TEST_DSN` to its test-only URL, and run
`python -m pytest -q tests/test_pg_connection.py`. Without that variable the
live test is skipped. This does not perform a service cutover, implement a
repository adapter or provide Controller leadership locking.
