"""Offline failure handling and live PostgreSQL 16 session ownership."""

import os
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, make_url

from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.storage.postgres_connection import (
    PostgresFactory,
    PostgresUnavailableError,
)


@pytest.fixture
def sessions() -> tuple[MagicMock, MagicMock]:
    factory = MagicMock(spec=PostgresFactory)
    connection = MagicMock(spec=Connection)
    connection.closed = False
    connection.invalidated = False
    connection.execute.return_value.scalar_one.return_value = True
    factory.connect_session.return_value = connection
    return factory, connection


@pytest.mark.parametrize("failure", ["connect", "query", "commit"])
def test_acquire_failure_retires_and_sanitizes(sessions, failure):
    factory, connection = sessions
    error = RuntimeError("SELECT secret FROM postgresql://user:secret@host/db")
    if failure == "connect":
        factory.connect_session.side_effect = error
    elif failure == "query":
        connection.execute.side_effect = error
    else:
        connection.begin.return_value.__exit__.side_effect = error

    lock = PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER)
    with pytest.raises(PostgresUnavailableError) as raised:
        lock.try_acquire()
    assert str(raised.value) == "PostgreSQL leadership acquisition failed"
    assert raised.value.__suppress_context__
    assert not lock.try_acquire()
    assert not lock.is_held()
    assert not lock.close()
    factory.connect_session.assert_called_once()
    if failure != "connect":
        connection.invalidate.assert_called_once()
        connection.close.assert_called_once()


@pytest.mark.parametrize("failure", ["closed", "invalidated", "query", "missing"])
def test_ownership_failure_never_reconnects(sessions, failure):
    factory, connection = sessions
    lock = PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER)
    assert lock.try_acquire()
    connection.execute.reset_mock()
    if failure in {"closed", "invalidated"}:
        setattr(connection, failure, True)
    elif failure == "query":
        connection.execute.side_effect = RuntimeError("secret")
    else:
        connection.execute.return_value.scalar_one.return_value = False

    assert not lock.is_held()
    assert not lock.try_acquire()
    assert not lock.close()
    factory.connect_session.assert_called_once()
    if failure in {"closed", "invalidated"}:
        connection.execute.assert_not_called()
    if failure != "closed":
        connection.invalidate.assert_called_once()
        connection.close.assert_called_once()


@pytest.mark.parametrize("failure", ["query", "missing", "close"])
def test_release_failure_discards_session(sessions, failure):
    factory, connection = sessions
    lock = PostgresLeadershipLock(factory, LeadershipDomain.CI_WATCHER)
    assert lock.try_acquire()
    if failure == "query":
        connection.execute.side_effect = RuntimeError("secret")
    elif failure == "missing":
        connection.execute.return_value.scalar_one.return_value = False
    else:
        connection.close.side_effect = RuntimeError("secret")

    assert not lock.release()
    assert not lock.release()
    assert not lock.try_acquire()
    assert not lock.is_held()
    connection.invalidate.assert_called_once()
    assert connection.close.call_count == (2 if failure == "close" else 1)
    factory.connect_session.assert_called_once()


@pytest.fixture
def pg_factory() -> Iterator[PostgresFactory]:
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    if make_url(dsn).database not in {"workflow_test", "workflow_ci"}:
        pytest.fail("leadership tests require workflow_test or workflow_ci")
    # Use a pool to verify that an uncertain session is discarded before reuse.
    engine = create_engine(
        dsn, connect_args={"connect_timeout": 5}, hide_parameters=True
    )
    with PostgresFactory(engine) as factory:
        with factory.transaction() as conn:
            assert 160000 <= int(conn.scalar(text("SHOW server_version_num"))) < 170000
        yield factory


def test_live_contention_release_and_reacquire(pg_factory):
    domain = LeadershipDomain.CONTROLLER
    with (
        PostgresLeadershipLock(pg_factory, domain) as owner,
        PostgresLeadershipLock(pg_factory, domain) as contender,
    ):
        assert owner.try_acquire()
        assert owner.try_acquire()  # Must not increment PostgreSQL's lock count.
        assert owner.is_held()
        assert not contender.try_acquire()
        assert not contender.is_held()
        assert owner.release()
        assert not owner.release()
        assert not owner.try_acquire()
        assert contender.try_acquire()
        assert contender.is_held()

    with PostgresLeadershipLock(pg_factory, domain) as successor:
        assert successor.try_acquire()
        assert successor.close()
        assert not successor.close()


def test_live_independent_domains(pg_factory):
    with (
        PostgresLeadershipLock(pg_factory, LeadershipDomain.CONTROLLER) as controller,
        PostgresLeadershipLock(pg_factory, LeadershipDomain.CI_WATCHER) as watcher,
        PostgresLeadershipLock(pg_factory, LeadershipDomain.CI_WATCHER) as contender,
    ):
        assert controller.try_acquire()
        assert watcher.try_acquire()
        assert not contender.try_acquire()
        assert watcher.release()
        assert contender.try_acquire()
        assert controller.is_held()


def test_live_backend_termination_retires_owner(pg_factory):
    domain = LeadershipDomain.CONTROLLER
    with (
        PostgresLeadershipLock(pg_factory, domain) as owner,
        PostgresLeadershipLock(pg_factory, domain) as successor,
    ):
        with pg_factory.connect_session() as owner_connection:
            with owner_connection.begin():
                pid = owner_connection.scalar(
                    text("SELECT pg_catalog.pg_backend_pid()")
                )
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(pg_factory, "connect_session", lambda: owner_connection)
                assert owner.try_acquire()

            with pg_factory.transaction() as observer:
                assert observer.scalar(
                    text("SELECT pg_catalog.pg_terminate_backend(:pid, 5000)"),
                    {"pid": pid},
                )
            assert not owner.is_held()
            assert owner_connection.closed
        assert not owner.try_acquire()
        assert not owner.release()
        assert successor.try_acquire()
        assert successor.is_held()


def test_live_failed_acquire_commit_discards_locked_session(pg_factory):
    domain = LeadershipDomain.CONTROLLER

    def fail_commit(connection: Connection) -> None:
        raise RuntimeError("injected failure after the session lock was granted")

    with PostgresLeadershipLock(pg_factory, domain) as owner:
        # Raise after successful lock SQL, before the transaction commit.
        with pg_factory.connect_session() as connection:
            driver_connection = connection.connection.driver_connection
            event.listen(connection, "commit", fail_commit)
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(pg_factory, "connect_session", lambda: connection)
                with pytest.raises(PostgresUnavailableError):
                    owner.try_acquire()
            assert connection.closed
            assert driver_connection.closed
        assert not owner.try_acquire()
        with PostgresLeadershipLock(pg_factory, domain) as successor:
            assert successor.try_acquire()
