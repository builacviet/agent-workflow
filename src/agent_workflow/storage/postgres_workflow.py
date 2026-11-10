"""PostgreSQL workflow storage adapter, opt-in and not yet cut over.

A transaction-scoped advisory lock retains the existing strict V1 single
writer transition semantics while native PostgreSQL constraints protect the
ledger across independent connections. No schema DDL runs as a service role.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from agent_workflow.budget import check_deadline, operation_timeout
from agent_workflow.storage.repository import SQLiteUnit

_NAMESPACE = 0x41474657
_TRANSACTION_KEY = 10


def _translate(query: str) -> str:
    """The workflow unit's current SQL uses only positional '?' placeholders.

    This bridge is scoped to reviewed internal SQL, never arbitrary operator SQL.
    It must be retired when the final PostgreSQL adapter owns its own statements.
    """
    return query.replace("?", "%s")


class _PGConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, query: str, values: tuple = ()):
        return self._connection.execute(_translate(query), values)


class PostgresUnit(SQLiteUnit):
    """Current portable workflow SQL with PostgreSQL dictionary rows."""

    def ci_approved(self, values: tuple = ()) -> bool:
        try:
            return super().ci_approved(values)
        except Exception:
            # Inaccessible/incompatible CI schema never opens the review gate.
            return False


class PostgresWorkflowRepository:
    """Trusted PostgreSQL workflow repository with no runtime migration DDL."""

    def __init__(self, dsn: str):
        if not isinstance(dsn, str) or not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("PostgreSQL DSN required")
        self.dsn = dsn

    @contextmanager
    def raw_connection(self) -> Iterator[_PGConnection]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            self.dsn, autocommit=True, row_factory=dict_row,
            connect_timeout=max(1, min(10, int(operation_timeout(10)))),
        ) as conn:
            conn.execute("SET search_path TO workflow, pg_catalog")
            yield _PGConnection(conn)

    @contextmanager
    def raw_transaction(self) -> Iterator[_PGConnection]:
        with self.raw_connection() as db:
            with db._connection.transaction():
                db._connection.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (_NAMESPACE, _TRANSACTION_KEY),
                )
                timeout_ms = max(1000, int(operation_timeout(30) * 1000))
                db._connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(timeout_ms),),
                )
                yield db
                check_deadline()

    @contextmanager
    def transaction(self) -> Iterator[PostgresUnit]:
        with self.raw_transaction() as db:
            yield PostgresUnit(db)

    @contextmanager
    def read(self) -> Iterator[PostgresUnit]:
        with self.raw_connection() as db:
            with db._connection.transaction():
                yield PostgresUnit(db)
