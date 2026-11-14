"""Explicit PostgreSQL runtime connections; importing does not connect."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from agent_workflow.app_config import AppConfig, validate_pg_runtime_dsn

EXPECTED_REVISION = "pg02d_webhook_digest"


class PostgresUnavailableError(RuntimeError):
    """Runtime connection or schema validation failed without exposing credentials."""


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


class PostgresFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._closed = False
        try:
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
    def repository_session(
        self, *, error_message: str, read_only: bool = False
    ) -> Iterator[Session]:
        """Scope Webhook/CI work and flush only before a write commit."""
        connection_scope = self.connect_session if read_only else self.transaction
        try:
            with connection_scope() as connection:
                _set_repository_transaction_options(connection, read_only=read_only)
                with Session(
                    bind=connection, join_transaction_mode="rollback_only"
                ) as session:
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
