import os
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text

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
    "rows", [[], ["other"], ["pg01_baseline"], ["pg02c_agent_dispatch", "other"]]
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
        "pg02c_agent_dispatch"
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
            text("INSERT INTO alembic_version VALUES ('pg02c_agent_dispatch')")
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


def test_disposable_live_transactions():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        table = "pg02_test_" + uuid.uuid4().hex
        try:
            with factory.transaction() as conn:
                conn.execute(text(f"CREATE TABLE {table} (value integer)"))
                conn.execute(text(f"INSERT INTO {table} VALUES (1)"))
            with pytest.raises(RuntimeError):
                with factory.transaction() as conn:
                    conn.execute(text(f"INSERT INTO {table} VALUES (2)"))
                    raise RuntimeError("rollback")
            with factory.transaction() as conn:
                assert conn.execute(
                    text(f"SELECT value FROM {table}")
                ).scalars().all() == [1]
        finally:
            with factory.transaction() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
