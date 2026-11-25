"""Own one synchronous PostgreSQL transaction and its scoped ORM Session."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from types import TracebackType
from typing import Literal, Self

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent_workflow.application.exceptions import PersistenceUnavailableError
from agent_workflow.budget import check_deadline, operation_timeout


class PostgresUnavailableError(PersistenceUnavailableError):
    """Runtime connection or transaction failed without exposing database values."""


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
        session: Session,
        *,
        read_only: bool = False,
        after_session_commit: Callable[[], None] | None = check_deadline,
    ) -> None:
        self._session = session
        self._read_only = read_only
        self._after_session_commit = after_session_commit
        self._scope: AbstractContextManager[None] | None = None
        self._decision: Literal["commit", "rollback"] | None = None

    @property
    def session(self) -> Session:
        if self._scope is None:
            raise RuntimeError("Unit of work is outside an active scope")
        return self._session

    def __enter__(self) -> Self:
        if self._scope is not None:
            raise RuntimeError("Unit of work is already active")
        scope = self._transaction()
        scope.__enter__()
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
    def _transaction(self) -> Iterator[None]:
        session = self._session
        connection = session.get_bind()
        if (
            not isinstance(connection, Connection)
            or session.join_transaction_mode != "rollback_only"
            or session.in_transaction()
            or connection.in_transaction()
        ):
            raise ValueError(
                "Unit of work requires an idle connection-bound Session in rollback_only mode"
            )

        def refresh_timeout(*_args: object) -> None:
            set_workflow_query_timeout(session)

        try:
            with connection.begin() as transaction:
                if self._read_only:
                    connection.execute(
                        text(
                            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                        )
                    )
                event.listen(session, "do_orm_execute", refresh_timeout)
                event.listen(session, "before_flush", refresh_timeout)
                try:
                    refresh_timeout()
                    yield
                    if self._decision == "commit":
                        refresh_timeout()
                        session.commit()
                        if self._after_session_commit is not None:
                            self._after_session_commit()
                finally:
                    event.remove(session, "before_flush", refresh_timeout)
                    event.remove(session, "do_orm_execute", refresh_timeout)
                    session.close()
                if self._decision == "commit":
                    transaction.commit()
                else:
                    transaction.rollback()
        except SQLAlchemyError:
            raise PostgresUnavailableError("PostgreSQL unit of work failed") from None
