import os
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from agent_workflow.app_config import AppConfig
from agent_workflow.infrastructure.database import session_factory
from agent_workflow.infrastructure.database.session_factory import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.observability.correlation import (
    correlation_scope,
    set_correlation_id,
)

DSN = "postgresql+psycopg://user:secret@localhost:5432/db"


@pytest.fixture
def fake_engine(monkeypatch):
    # Keep a real SQLAlchemy event target while stubbing out DB behavior.
    engine = create_engine("sqlite://")
    monkeypatch.setattr(engine, "connect", MagicMock())
    monkeypatch.setattr(engine, "dispose", MagicMock())
    return engine


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
def test_bad_revision_disposes(rows, fake_engine):
    engine = fake_engine
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalars.return_value.all.return_value = rows
    with pytest.raises(PostgresUnavailableError):
        PostgresFactory(engine)
    engine.dispose.assert_called_once()
    engine.connect.return_value.__exit__.assert_called_once()


def test_unreachable_is_safe_and_disposes(monkeypatch, fake_engine):
    engine = fake_engine
    engine.connect.side_effect = RuntimeError(DSN)
    monkeypatch.setattr(session_factory, "create_engine", lambda *a, **kw: engine)
    with pytest.raises(PostgresUnavailableError) as exc:
        PostgresFactory.open(AppConfig(pg_runtime_dsn=DSN))
    assert "secret" not in str(exc.value)
    assert engine.dispose.called


def test_missing_version_table_fails_closed(fake_engine):
    engine = fake_engine
    engine.connect.return_value.__enter__.return_value.execute.side_effect = (
        RuntimeError("missing table")
    )
    with pytest.raises(PostgresUnavailableError):
        PostgresFactory(engine)
    engine.dispose.assert_called_once()


def test_open_uses_bounded_timeout_and_checks_revision(monkeypatch, fake_engine):
    engine = fake_engine
    engine.connect.return_value.__enter__.return_value.execute.return_value.scalars.return_value.all.return_value = [
        "pg02d_webhook_digest"
    ]
    create = MagicMock(return_value=engine)
    monkeypatch.setattr(session_factory, "create_engine", create)
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


def test_correlation_listener_registers_before_readiness_and_only_once(monkeypatch):
    engine = create_engine("sqlite://")

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num text)"))
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:version)"),
            {"version": session_factory.EXPECTED_REVISION},
        )

    statements = []
    listen = event.listen

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    install = MagicMock(wraps=listen)

    def register(target, name, listener, **options):
        install(target, name, listener, **options)
        listen(target, name, observe)

    monkeypatch.setattr(event, "listen", register)
    try:
        with correlation_scope(correlation_id="readiness-1"):
            first = PostgresFactory(engine)
            second = PostgresFactory(engine)
            statement = text("SELECT :number")
            with second.connect_session() as connection:
                assert connection.scalar(statement, {"number": 42}) == 42
                prefixed = text(
                    "/* correlation_id=prior */ /* correlation_id=older */ SELECT :number"
                )
                with correlation_scope(correlation_id="nested"):
                    assert connection.scalar(prefixed, {"number": 1}) == 1
                assert connection.scalar(prefixed, {"number": 2}) == 2
        with first.connect_session() as connection:
            assert connection.scalar(prefixed, {"number": 3}) == 3
        install.assert_called_once()
        assert install.call_args.kwargs == {"retval": True}
        assert statements == [
            (
                "/* correlation_id=readiness-1 */ SELECT version_num FROM alembic_version",
                (),
            ),
            (
                "/* correlation_id=readiness-1 */ SELECT version_num FROM alembic_version",
                (),
            ),
            ("/* correlation_id=readiness-1 */ SELECT ?", (42,)),
            ("/* correlation_id=nested */ SELECT ?", (1,)),
            ("/* correlation_id=readiness-1 */ SELECT ?", (2,)),
            ("SELECT ?", (3,)),
        ]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "correlation_id", [None, "", "x*/y", "newline\n", "a b", "x" * 129, "é"]
)
def test_correlation_listener_leaves_sql_and_parameters_untouched_for_invalid_id(
    correlation_id,
):
    statement = "SELECT %(bound)s"
    parameters = {"bound": "private-value"}
    with correlation_scope():
        set_correlation_id(correlation_id)
        sql, bound = session_factory._correlate_statement(
            None, None, statement, parameters, None, False
        )
    assert sql == statement
    assert bound is parameters


@pytest.mark.parametrize("correlation_id", [None, "active"])
def test_correlation_listener_preserves_parameter_object_when_replacing_prefix(
    correlation_id,
):
    statement = "SELECT %(bound)s"
    parameters = {"bound": "bound value"}
    with correlation_scope():
        set_correlation_id(correlation_id)
        sql, bound = session_factory._correlate_statement(
            None,
            None,
            "/* correlation_id=prior */ " + statement,
            parameters,
            None,
            False,
        )
    expected = (
        f"/* correlation_id={correlation_id} */ " + statement
        if correlation_id
        else statement
    )
    assert sql == expected
    assert bound is parameters


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
            with factory.transaction() as connection:
                connection.execute(insert_value, {"value": 1})
            assert outcomes == ["commit", "close"]
            assert connection.closed
            outcomes.clear()

            with pytest.raises(RuntimeError, match="rollback"):
                with factory.transaction() as connection:
                    connection.execute(insert_value, {"value": 2})
                    raise RuntimeError("rollback")
            assert outcomes == ["rollback", "close"]
            assert connection.closed
            outcomes.clear()

            with factory.connect_session() as connection:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                assert connection.scalar(text("SHOW transaction_read_only")) == "on"
                assert (
                    connection.execute(text(f"SELECT value FROM {table}"))
                    .scalars()
                    .all()
                    == [1]
                )
            assert outcomes == ["rollback", "close"]
            assert connection.closed
            outcomes.clear()

            with pytest.raises(DBAPIError) as exc:
                with factory.connect_session() as connection:
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    connection.execute(insert_value, {"value": 3})
            assert getattr(exc.value.orig, "sqlstate", None) == "25006"
            assert outcomes == ["rollback", "close"]
            assert connection.closed
            with factory.connect_session() as connection:
                assert (
                    connection.execute(text(f"SELECT value FROM {table}"))
                    .scalars()
                    .all()
                    == [1]
                )
        finally:
            with factory.transaction() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
