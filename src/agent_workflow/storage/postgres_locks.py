"""PostgreSQL session advisory lock for independent trusted Controllers.

Wiring to the Controller entry point must happen with the PostgreSQL adapter;
the current SQLite path keeps its existing process lock until cutover.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator, Protocol

from agent_workflow.integrations.github import WorkflowError

# Distinct application namespace + role keys, stable across processes/hosts.
_LOCK_NAMESPACE = 0x41474657  # "AGFW" (signed int32)
_ROLE_KEYS = {"dev_controller": 1, "ci_watcher": 2}


class _Cursor(Protocol):
    def execute(self, query: str, parameters: tuple[int, int] = ()) -> object: ...
    def fetchone(self) -> tuple[bool] | None: ...
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, tb) -> object: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...
    def close(self) -> None: ...


class AdvisoryLockLease:
    def __init__(self, connection: _Connection, key: int):
        self._conn, self._key = connection, key

    def ensure_alive(self) -> None:
        """Require the same still-live PostgreSQL session before side effects."""
        try:
            with self._conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise WorkflowError("PostgreSQL lock session no longer verifiable")
        except Exception:
            raise WorkflowError("PostgreSQL advisory lock session lost") from None


@contextmanager
def controller_advisory_lock(
    dsn: str, role: str, *, connect: Callable | None = None,
) -> Iterator[AdvisoryLockLease]:
    """Hold one distinct session lock per trusted Controller role.

    Keep this context open for the process lifetime. Never share this session
    with pooled application queries or run in autocommit=False. A dropped
    connection releases the server-side lock; ensure_alive() fails closed.
    """
    if role not in _ROLE_KEYS:
        raise ValueError("unsupported PostgreSQL Controller role")
    if not isinstance(dsn, str) or not dsn:
        raise ValueError("PostgreSQL connection string required")
    if connect is None:
        import psycopg
        connect = psycopg.connect

    conn = connect(dsn, autocommit=True)
    key = _ROLE_KEYS[role]
    acquired = False
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                (_LOCK_NAMESPACE, key),
            )
            acquired = cursor.fetchone() == (True,)
        if not acquired:
            raise WorkflowError("another trusted Controller holds this role lock")
        lease = AdvisoryLockLease(conn, key)
        lease.ensure_alive()
        yield lease
    finally:
        if acquired:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s, %s)",
                        (_LOCK_NAMESPACE, key),
                    )
            except Exception:
                pass  # Closing a PostgreSQL session releases its locks.
        conn.close()
