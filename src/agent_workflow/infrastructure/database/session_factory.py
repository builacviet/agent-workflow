"""Explicit PostgreSQL runtime connections; importing does not connect."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Lock

from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from agent_workflow.app_config import AppConfig, validate_pg_runtime_dsn
from agent_workflow.infrastructure.database.unit_of_work import (
    PostgresUnavailableError as PostgresUnavailableError,
    _set_repository_transaction_options as _set_repository_transaction_options,
)
from agent_workflow.observability.correlation import (
    get_correlation_id,
    is_valid_correlation_id,
)

EXPECTED_REVISION = "pg02d_webhook_digest"
_CORRELATION_COMMENT = re.compile(
    r"\A(?:/\* correlation_id=[A-Za-z0-9_-]{1,128} \*/ )+"
)
_LISTENER_LOCK = Lock()


def _correlate_statement(
    _connection: Connection,
    _cursor: object,
    statement: str,
    parameters: object,
    _context: object,
    _executemany: bool,
) -> tuple[str, object]:
    # A cached or caller-supplied correlation prefix cannot outlive its context.
    statement = _CORRELATION_COMMENT.sub("", statement, count=1)
    correlation_id = get_correlation_id()
    if is_valid_correlation_id(correlation_id):
        statement = f"/* correlation_id={correlation_id} */ {statement}"
    return statement, parameters


def _register_correlation_listener(engine: Engine) -> None:
    with _LISTENER_LOCK:
        if not event.contains(engine, "before_cursor_execute", _correlate_statement):
            event.listen(
                engine, "before_cursor_execute", _correlate_statement, retval=True
            )


class PostgresFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._closed = False
        try:
            _register_correlation_listener(engine)
            self.assert_schema_ready()
        except Exception:
            engine.dispose()
            raise

    @classmethod
    def open(cls, config: AppConfig) -> PostgresFactory:
        if config.pg_runtime_dsn is None:
            raise ValueError("PG_RUNTIME_DSN is required")
        dsn = validate_pg_runtime_dsn(config.pg_runtime_dsn)
        try:
            engine = create_engine(
                dsn,
                poolclass=NullPool,
                connect_args={"connect_timeout": 5},
                hide_parameters=True,
            )
        except Exception:
            raise PostgresUnavailableError(
                "PostgreSQL connection or schema readiness check failed"
            ) from None
        return cls(engine)

    def assert_schema_ready(self) -> None:
        if self._closed:
            raise PostgresUnavailableError("PostgreSQL factory is closed")
        try:
            with self._engine.connect() as connection:
                revisions = (
                    connection.execute(text("SELECT version_num FROM alembic_version"))
                    .scalars()
                    .all()
                )
                if revisions != [EXPECTED_REVISION]:
                    raise ValueError("unexpected schema revision")
        except Exception:
            raise PostgresUnavailableError(
                "PostgreSQL connection or schema readiness check failed"
            ) from None

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """Commit successful work, roll back exceptions, and always close."""
        with self.connect_session() as connection:
            with connection.begin():
                yield connection

    @contextmanager
    def get_sync_session(self, connection: Connection) -> Iterator[Session]:
        """Lend an ORM Session on the caller's connection and close it on exit."""
        with Session(bind=connection, join_transaction_mode="rollback_only") as session:
            yield session

    @contextmanager
    def get_scoped_session(self) -> Iterator[Session]:
        """Yield a fresh Session bound to an idle connection."""
        with (
            self.connect_session() as connection,
            self.get_sync_session(connection) as session,
        ):
            yield session

    @contextmanager
    def unit_of_work_session(
        self,
        *,
        read_only: bool = False,
        after_session_commit: Callable[[], None] | None = None,
    ) -> Iterator[Session]:
        """Compatibility scope for callers that commit on successful return."""
        with self.connect_session() as connection, connection.begin() as transaction:
            if read_only:
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
            with self.get_sync_session(connection) as session:
                yield session
                if not read_only:
                    session.commit()
                    if after_session_commit is not None:
                        after_session_commit()
            transaction.commit()

    @contextmanager
    def repository_session(
        self, *, error_message: str, read_only: bool = False
    ) -> Iterator[Session]:
        """Scope Webhook/CI work and flush only before a write commit."""
        connection_scope = self.connect_session if read_only else self.transaction
        try:
            with connection_scope() as connection:
                _set_repository_transaction_options(connection, read_only=read_only)
                with self.get_sync_session(connection) as session:
                    yield session
                    if not read_only:
                        session.flush()
        except (SQLAlchemyError, ValidationError):
            # Database and DTO errors can contain query parameters or row values.
            raise PostgresUnavailableError(error_message) from None

    def connect_session(self) -> Connection:
        """Check out a connection until the caller closes or invalidates it."""
        if self._closed:
            raise PostgresUnavailableError("PostgreSQL factory is closed")
        try:
            return self._engine.connect()
        except Exception:
            raise PostgresUnavailableError("PostgreSQL connection failed") from None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._engine.dispose()

    def __enter__(self) -> PostgresFactory:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
