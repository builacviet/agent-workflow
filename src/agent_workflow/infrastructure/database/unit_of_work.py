"""Own one synchronous PostgreSQL transaction and its scoped ORM Session."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from types import TracebackType
from typing import TYPE_CHECKING, Literal, Self

from sqlalchemy import text
from sqlalchemy.orm import Session

from agent_workflow.budget import check_deadline, operation_timeout

if TYPE_CHECKING:
    from agent_workflow.infrastructure.database.session_factory import PostgresFactory


def set_workflow_query_timeout(session: Session) -> None:
    # PostgreSQL interprets zero as unlimited; never round a budget down to zero.
    milliseconds = max(1, int(operation_timeout(30) * 1000))
    session.connection().execute(
        text(
            "SELECT set_config('lock_timeout', :timeout, true), "
            "set_config('statement_timeout', :timeout, true)"
        ),
        {"timeout": f"{milliseconds}ms"},
    )


class SqlAlchemyUnitOfWork:
    def __init__(
        self,
        factory: PostgresFactory,
        *,
        read_only: bool = False,
        after_session_commit: Callable[[], None] | None = check_deadline,
    ) -> None:
        self._factory = factory
        self._read_only = read_only
        self._after_session_commit = after_session_commit
        self._scope: AbstractContextManager[Session] | None = None
        self._session: Session | None = None
        self._decision: Literal["commit", "rollback"] | None = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work is outside an active scope")
        return self._session

    def __enter__(self) -> Self:
        if self._scope is not None:
            raise RuntimeError("Unit of work is already active")
        scope = self._open_scope()
        self._session = scope.__enter__()
        self._scope = scope
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._scope is not None
        try:
            self._scope.__exit__(exc_type, exc_value, traceback)
        finally:
            self._session = None
            self._scope = None
            self._decision = None

    def commit(self) -> None:
        if self._read_only:
            raise RuntimeError("Cannot commit a read-only unit of work")
        self._decide("commit")

    def rollback(self) -> None:
        self._decide("rollback")

    def _decide(self, decision: Literal["commit", "rollback"]) -> None:
        if self._scope is None:
            raise RuntimeError("Unit of work is outside an active scope")
        if decision == "commit" and self._decision is not None:
            raise RuntimeError("Unit of work already has a transaction decision")
        self._decision = decision

    @contextmanager
    def _open_scope(self) -> Iterator[Session]:
        with self._factory.connect_session() as connection, connection.begin() as tx:
            if self._read_only:
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
            with self._factory.get_sync_session(connection) as session:
                yield session
                if self._decision == "commit" and not self._read_only:
                    # rollback_only keeps the physical transaction open for the hook.
                    session.commit()
                    if self._after_session_commit is not None:
                        self._after_session_commit()
            if self._decision == "commit":
                tx.commit()
            else:
                tx.rollback()

    @classmethod
    @contextmanager
    def legacy_session(
        cls,
        factory: PostgresFactory,
        *,
        read_only: bool,
        after_session_commit: Callable[[], None] | None,
    ) -> Iterator[Session]:
        """Keep success-based completion for legacy Workflow and Dispatch."""
        with cls(
            factory, read_only=read_only, after_session_commit=after_session_commit
        ) as unit:
            yield unit.session
            # Legacy reads end with an outer commit, without an ORM commit or flush.
            unit._decide("commit")
