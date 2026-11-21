"""Reusable PostgreSQL CI leadership provider for Application scope injection.

Only this infrastructure adapter allocates a fresh advisory lock per pass.
Every pass retains its own session and retires it on all exit paths.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from agent_workflow.application.exceptions import ApplicationError
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.storage.postgres_connection import PostgresFactory


class PostgresCiLeadershipScope:
    def __init__(self, factory: PostgresFactory) -> None:
        self._factory = factory
        self._active: ContextVar[PostgresLeadershipLock | None] = ContextVar(
            "ci_active_leadership", default=None
        )

    @contextmanager
    def acquired_scope(self) -> Iterator[None]:
        """Fail closed on contention or lost ownership; no shared retired lock."""
        lock = PostgresLeadershipLock(self._factory, LeadershipDomain.CI_WATCHER)
        try:
            if not lock.try_acquire():
                raise ApplicationError("PostgreSQL CI watcher leadership unavailable")
            token = self._active.set(lock)
            try:
                self.require_owner()
                yield
                self.require_owner()
            finally:
                self._active.reset(token)
        finally:
            lock.release()

    def require_owner(self) -> None:
        lock = self._active.get()
        if lock is None or lock.is_held() is not True:
            raise PermissionError("PostgreSQL leadership lost; reconcile in-flight work")
