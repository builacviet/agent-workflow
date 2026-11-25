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


def _set_repository_transaction_options(
    connection: Connection, *, read_only: bool
) -> None:
    # Webhook collision lookup must see the winner committed by a concurrent insert.
    mode = ", READ ONLY" if read_only else ""
    connection.execute(text(f"SET TRANSACTION ISOLATION LEVEL READ COMMITTED{mode}"))
    connection.execute(
        text(
            "SELECT set_config('lock_timeout', :lock_timeout, true), "
            "set_config('statement_timeout', :statement_timeout, true), "
            "set_config('synchronous_commit', :synchronous_commit, true)"
        ),
        {
            "lock_timeout": "5s",
            "statement_timeout": "5s",
            "synchronous_commit": "on",
        },
    )


class SqlAlchemyUnitOfWork:
    def __init__(
        self,
        session: Session,
        *,
        read_only: bool = False,
        profile: Literal["workflow", "ci_write"] = "workflow",
        after_session_commit: Callable[[], None] | None = check_deadline,
    ) -> None:
        if profile not in ("workflow", "ci_write"):
            raise ValueError("Unknown transaction profile")
        if profile == "ci_write" and read_only:
            raise ValueError("CI write profile cannot be read-only")
        self._session = session
        self._read_only = read_only
        self._profile = profile
        # CI writes retain their fixed short scope, not the dispatch budget.
        self._after_session_commit = (
            None
            if profile == "ci_write" and after_session_commit is check_deadline
            else after_session_commit
        )
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
                if self._profile == "workflow":
                    event.listen(session, "do_orm_execute", refresh_timeout)
                    event.listen(session, "before_flush", refresh_timeout)
                try:
                    if self._profile == "ci_write":
                        _set_repository_transaction_options(connection, read_only=False)
                    else:
                        refresh_timeout()
                    yield
                    if self._decision == "commit":
                        if self._profile == "workflow":
                            refresh_timeout()
                        session.commit()
                        if self._after_session_commit is not None:
                            self._after_session_commit()
                finally:
                    if self._profile == "workflow":
                        event.remove(session, "before_flush", refresh_timeout)
                        event.remove(session, "do_orm_execute", refresh_timeout)
                    session.close()
                if self._decision == "commit":
                    transaction.commit()
                else:
                    transaction.rollback()
            # Successful implicit rollback can return a read or replay result.
            # Check only after cleanup; never replace an error or explicit rollback.
            if self._profile == "workflow" and self._decision is None:
                check_deadline()
        except SQLAlchemyError:
            raise PostgresUnavailableError("PostgreSQL unit of work failed") from None
