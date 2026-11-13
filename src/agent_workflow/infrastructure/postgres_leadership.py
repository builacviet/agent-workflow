"""Session ownership for PostgreSQL-backed workflow services."""

from __future__ import annotations

from enum import IntEnum

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import TextClause

from agent_workflow.storage.postgres_connection import (
    PostgresFactory,
    PostgresUnavailableError,
)

# Use PostgreSQL's two-integer advisory key space. Keep these values stable.
_WORKFLOW_NAMESPACE = 0x41574631  # AWF1


class LeadershipDomain(IntEnum):
    CONTROLLER = 1
    CI_WATCHER = 2


_TRY_LOCK = text("SELECT pg_catalog.pg_try_advisory_lock(:namespace, :domain)")
_UNLOCK = text("SELECT pg_catalog.pg_advisory_unlock(:namespace, :domain)")
_HOLDS_LOCK = text("""
    SELECT EXISTS (
        SELECT 1 FROM pg_catalog.pg_locks
        WHERE locktype = 'advisory' AND pid = pg_catalog.pg_backend_pid()
          AND classid = :namespace AND objid = :domain AND objsubid = 2
          AND mode = 'ExclusiveLock' AND granted
    )
""")


class PostgresLeadershipLock:
    """One ownership lifetime on a dedicated session.

    Use from one caller at a time. Contention permits another try. Loss, a
    database error, or explicit release retires this instance. Callers must
    check ownership before work and stop when the check fails.
    """

    def __init__(self, factory: PostgresFactory, domain: LeadershipDomain) -> None:
        self._factory = factory
        self._key = {
            "namespace": _WORKFLOW_NAMESPACE,
            "domain": int(LeadershipDomain(domain)),
        }
        self._connection: Connection | None = None
        self._retired = False

    def try_acquire(self) -> bool:
        """Try once without waiting for the lock. Raise on database failure."""
        if self._retired:
            return False
        if self._connection is not None:
            return self.is_held()

        connection = None
        completed = False
        try:
            connection = self._factory.connect_session()
            acquired = self._query(connection, _TRY_LOCK)
            if acquired:
                self._connection = connection
            else:
                connection.close()
            completed = True
            return acquired
        except Exception:
            raise PostgresUnavailableError(
                "PostgreSQL leadership acquisition failed"
            ) from None
        finally:
            if not completed:
                self._retired = True
                self._connection = None
                if connection is not None:
                    self._discard(connection)

    def is_held(self) -> bool:
        """Check the original session for ownership and retire on failure."""
        connection = self._connection
        if connection is None:
            return False
        held = False
        try:
            held = self._query(connection, _HOLDS_LOCK)
            return held
        except Exception:
            return False
        finally:
            if not held:
                self._retired = True
                self._connection = None
                self._discard(connection)

    def release(self) -> bool:
        """Retire the instance. Return True only after successful unlock and close."""
        self._retired = True
        connection, self._connection = self._connection, None
        if connection is None:
            return False
        closed = False
        try:
            if not self._query(connection, _UNLOCK):
                return False
            connection.close()
            closed = True
            return True
        except Exception:
            return False
        finally:
            if not closed:
                self._discard(connection)

    close = release

    def __enter__(self) -> PostgresLeadershipLock:
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    def _query(self, connection: Connection, statement: TextClause) -> bool:
        if connection.closed or connection.invalidated:
            raise PostgresUnavailableError(
                "PostgreSQL leadership session is unavailable"
            )
        with connection.begin():
            connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            return connection.execute(statement, self._key).scalar_one() is True

    @staticmethod
    def _discard(connection: Connection) -> None:
        # The session may still hold the lock after a failed query. Invalidate
        # before closing so the pool cannot lend that session to another caller.
        if connection.closed:
            return
        try:
            try:
                connection.invalidate()
            finally:
                connection.close()
        except Exception:
            pass
