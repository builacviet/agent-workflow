import os
import uuid
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from agent_workflow.app_config import AppConfig
from agent_workflow.storage.postgres_connection import (
    PostgresFactory,
    PostgresUnavailableError,
)

DSN = "postgresql+psycopg://user:secret@localhost:5432/db"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "postgresql://u:secret@host/db",
        "postgresql+psycopg://u@host/db",
        "postgresql+psycopg://u:secret@host/",
        "postgresql+psycopg://u:secret@host/db?host=other",
        "postgresql+psycopg://u:secret@host:0/db",
        "postgresql+psycopg://u:secret@host/db#x",
        "postgresql+psycopg://u:secret@host/db%ZZ",
    ],
)
def test_invalid_dsn_is_safe(value):
    with pytest.raises(ValueError) as exc:
        AppConfig.from_env({"PG_RUNTIME_DSN": value})
    assert "secret" not in str(exc.value)


def test_opt_in_and_repr():
    assert AppConfig.from_env({}).pg_runtime_dsn is None
    config = AppConfig.from_env({"PG_RUNTIME_DSN": DSN, "PG_MIGRATOR_DSN": "ignored"})
    assert config.pg_runtime_dsn == DSN
    assert "secret" not in repr(config)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        ["other"],
        ["pg01_baseline"],
        ["pg02c_agent_dispatch"],
        ["pg02d_webhook_digest", "other"],
    ],
)
def test_bad_revision_disposes(rows):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalars.return_value.all.return_value = rows
    with pytest.raises(PostgresUnavailableError):
        PostgresFactory(engine)
    engine.dispose.assert_called_once()
    engine.connect.return_value.__exit__.assert_called_once()


def test_unreachable_is_safe_and_disposes(monkeypatch):
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError(DSN)
    monkeypatch.setattr(
        "agent_workflow.storage.postgres_connection.create_engine",
        lambda *a, **kw: engine,
    )
    with pytest.raises(PostgresUnavailableError) as exc:
        PostgresFactory.open(AppConfig(pg_runtime_dsn=DSN))
    assert "secret" not in str(exc.value)
    assert engine.dispose.called


def test_missing_version_table_fails_closed():
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.side_effect = (
        RuntimeError("missing table")
    )
    with pytest.raises(PostgresUnavailableError):
        PostgresFactory(engine)
    engine.dispose.assert_called_once()


def test_open_uses_bounded_timeout_and_checks_revision(monkeypatch):
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalars.return_value.all.return_value = [
        "pg02d_webhook_digest"
    ]
    create = MagicMock(return_value=engine)
    monkeypatch.setattr(
        "agent_workflow.storage.postgres_connection.create_engine", create
    )
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=DSN)):
        assert create.call_args.kwargs["connect_args"] == {"connect_timeout": 5}
    engine.dispose.assert_called_once()


def test_transaction_commit_rollback_and_close():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num text)"))
        conn.execute(
            text("INSERT INTO alembic_version VALUES ('pg02d_webhook_digest')")
        )
        conn.execute(text("CREATE TABLE items (value integer)"))
    factory = PostgresFactory(engine)
    with factory.transaction() as conn:
        conn.execute(text("INSERT INTO items VALUES (1)"))
    with pytest.raises(RuntimeError):
        with factory.transaction() as conn:
            conn.execute(text("INSERT INTO items VALUES (2)"))
            raise RuntimeError("rollback")
    with factory.transaction() as conn:
        assert conn.execute(text("SELECT value FROM items")).scalars().all() == [1]
    factory.close()
    with pytest.raises(PostgresUnavailableError):
        with factory.transaction():
            pytest.fail("closed factory must not connect")


@pytest.mark.parametrize(
    "failure", [None, "connect", "settings", "body", "dto", "flush", "commit"]
)
def test_repository_session_lifecycle(monkeypatch, failure):
    class DTO(BaseModel):
        value: int

    engine = MagicMock()
    connection_scope = engine.connect.return_value
    connection = connection_scope.__enter__.return_value
    connection.execute.return_value.scalars.return_value.all.return_value = [
        "pg02d_webhook_digest"
    ]
    factory = PostgresFactory(engine)
    engine.reset_mock()
    transaction = connection.begin.return_value
    session_type = MagicMock()
    session_scope = session_type.return_value
    session = session_scope.__enter__.return_value
    lifecycle = MagicMock()
    lifecycle.attach_mock(session, "session")
    lifecycle.attach_mock(session_scope, "session_scope")
    lifecycle.attach_mock(transaction, "transaction")
    lifecycle.attach_mock(connection_scope, "connection_scope")
    monkeypatch.setattr(
        "agent_workflow.storage.postgres_connection.Session", session_type
    )

    database_error = SQLAlchemyError(DSN)
    if failure == "connect":
        engine.connect.side_effect = RuntimeError(DSN)
    elif failure == "settings":
        connection.execute.side_effect = database_error
    elif failure == "flush":
        session.flush.side_effect = database_error
    elif failure == "commit":
        transaction.__exit__.side_effect = database_error
    domain_error = RuntimeError("domain error")

    def run_operation():
        with factory.repository_session(error_message="operation failed") as opened:
            assert opened is session
            if failure == "body":
                raise domain_error
            if failure == "dto":
                DTO(value=DSN)

    if failure is None:
        run_operation()
    elif failure == "body":
        with pytest.raises(RuntimeError) as exc:
            run_operation()
        assert exc.value is domain_error
    else:
        message = (
            "PostgreSQL connection failed"
            if failure == "connect"
            else "operation failed"
        )
        with pytest.raises(PostgresUnavailableError, match=f"^{message}$") as exc:
            run_operation()
        assert exc.value.__suppress_context__

    if failure == "connect":
        connection_scope.__enter__.assert_not_called()
        session_type.assert_not_called()
        return

    expected_error = {
        "settings": SQLAlchemyError,
        "body": RuntimeError,
        "dto": ValidationError,
        "flush": SQLAlchemyError,
    }.get(failure)
    transaction.__exit__.assert_called_once()
    assert transaction.__exit__.call_args.args[0] is expected_error
    connection_scope.__exit__.assert_called_once()
    if failure == "settings":
        session_type.assert_not_called()
        return

    session_type.assert_called_once_with(
        bind=connection, join_transaction_mode="rollback_only"
    )
    session_scope.__exit__.assert_called_once()
    assert session_scope.__exit__.call_args.args[0] is expected_error
    calls = [call[0] for call in lifecycle.mock_calls]
    assert calls.index("session_scope.__exit__") < calls.index("transaction.__exit__")
    assert calls.index("transaction.__exit__") < calls.index(
        "connection_scope.__exit__"
    )
    session.commit.assert_not_called()
    if failure in (None, "flush", "commit"):
        session.flush.assert_called_once()
        assert calls.index("session.flush") < calls.index("session_scope.__exit__")
    else:
        session.flush.assert_not_called()


def test_repository_read_session_closes_without_flush_or_commit(monkeypatch):
    engine = MagicMock()
    connection_scope = engine.connect.return_value
    connection = connection_scope.__enter__.return_value
    connection.execute.return_value.scalars.return_value.all.return_value = [
        "pg02d_webhook_digest"
    ]
    factory = PostgresFactory(engine)
    connection_scope.reset_mock()
    session_type = MagicMock()
    monkeypatch.setattr(
        "agent_workflow.storage.postgres_connection.Session", session_type
    )

    with factory.repository_session(
        error_message="read failed", read_only=True
    ) as session:
        assert session is session_type.return_value.__enter__.return_value

    assert str(connection.execute.call_args_list[0].args[0]) == (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"
    )
    connection.begin.assert_not_called()
    connection.commit.assert_not_called()
    session.flush.assert_not_called()
    session_scope = session_type.return_value
    session_scope.__exit__.assert_called_once_with(None, None, None)
    connection_scope.__exit__.assert_called_once_with(None, None, None)


def test_disposable_live_transactions():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True)
    with PostgresFactory(engine) as factory:
        table = "pg02_test_" + uuid.uuid4().hex
        try:
            with factory.transaction() as conn:
                conn.execute(text(f"CREATE TABLE {table} (value integer)"))
            outcomes = []
            event.listen(engine, "commit", lambda conn: outcomes.append("commit"))
            event.listen(engine, "rollback", lambda conn: outcomes.append("rollback"))
            event.listen(
                engine, "close", lambda connection, record: outcomes.append("close")
            )
            insert_value = text(f"INSERT INTO {table} VALUES (:value)")
            with factory.repository_session(
                error_message="test insert failed"
            ) as session:
                for setting, expected in (
                    ("transaction_isolation", "read committed"),
                    ("lock_timeout", "5s"),
                    ("statement_timeout", "5s"),
                    ("synchronous_commit", "on"),
                ):
                    assert (
                        session.scalar(
                            text("SELECT current_setting(:setting)"),
                            {"setting": setting},
                        )
                        == expected
                    )
                session.execute(insert_value, {"value": 1})
            assert outcomes == ["commit", "close"]
            outcomes.clear()
            with pytest.raises(RuntimeError):
                with factory.repository_session(
                    error_message="test insert failed"
                ) as session:
                    session.execute(insert_value, {"value": 2})
                    raise RuntimeError("rollback")
            assert outcomes == ["rollback", "close"]
            outcomes.clear()
            with factory.repository_session(
                error_message="test read failed", read_only=True
            ) as session:
                assert session.scalar(text("SHOW transaction_read_only")) == "on"
                assert session.execute(
                    text(f"SELECT value FROM {table}")
                ).scalars().all() == [1]
            assert outcomes == ["rollback", "close"]
            outcomes.clear()
            with pytest.raises(PostgresUnavailableError, match="^test read failed$"):
                with factory.repository_session(
                    error_message="test read failed", read_only=True
                ) as session:
                    session.execute(insert_value, {"value": 3})
            assert outcomes == ["rollback", "close"]
            with factory.transaction() as conn:
                assert conn.execute(
                    text(f"SELECT value FROM {table}")
                ).scalars().all() == [1]
        finally:
            with factory.transaction() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
