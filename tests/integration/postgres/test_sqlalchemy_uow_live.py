"""Verify the scoped Session against physical PostgreSQL transaction outcomes."""

import os
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agent_workflow import budget
from agent_workflow.app_config import AppConfig
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.infrastructure.database.models import Task
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import (
    PostgresUnavailableError,
    SqlAlchemyUnitOfWork,
)


@pytest.fixture
def transaction_task() -> Iterator[tuple[PostgresFactory, Task]]:
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    task_id = f"R2-DB-02-{uuid.uuid4().hex}"
    task = Task(
        task_id=task_id,
        metadata_text='{"context_paths":[]}',
        repository="builacviet/agent-workflow",
        branch="codex/test",
        base_sha="a" * 40,
        requested_effort="medium",
        require_verified_effort=0,
        status=WorkflowStatus.AWAITING_HANDOFF.value,
        created_at=1000.0,
        updated_at=1000.0,
    )
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        try:
            yield factory, task
        finally:
            with factory.transaction() as connection:
                connection.execute(delete(Task).where(Task.task_id == task_id))


def observe_transaction(session: Session) -> tuple[Connection, list[str]]:
    connection = session.connection()
    outcomes: list[str] = []
    event.listen(connection, "commit", lambda _: outcomes.append("commit"))
    event.listen(connection, "rollback", lambda _: outcomes.append("rollback"))
    return connection, outcomes


def is_visible(factory: PostgresFactory, task_id: str) -> bool:
    with factory.connect_session() as observer:
        persisted_id = observer.scalar(
            select(Task.task_id).where(Task.task_id == task_id)
        )
        return persisted_id is not None


def test_legacy_read_scope_retains_read_only_profile_and_outer_commit(
    transaction_task: tuple[PostgresFactory, Task],
) -> None:
    factory, _ = transaction_task
    with factory.unit_of_work_session(read_only=True) as session:
        connection, outcomes = observe_transaction(session)
        assert session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert session.scalar(text("SHOW transaction_read_only")) == "on"
        assert outcomes == []

    assert outcomes == ["commit"]
    assert connection.closed
    assert not session.in_transaction()


@pytest.mark.parametrize("decision", ["commit", "unset", "rollback", "cancel commit"])
def test_observer_sees_only_explicit_commit_and_connection_closes(
    transaction_task: tuple[PostgresFactory, Task], decision: str
) -> None:
    factory, task = transaction_task
    task_id = task.task_id
    with factory.get_scoped_session() as session, SqlAlchemyUnitOfWork(session) as unit:
        session = unit.session
        connection, outcomes = observe_transaction(session)
        session.add(task)
        session.flush()
        assert not is_visible(factory, task_id)
        if decision == "commit":
            unit.commit()
            assert not is_visible(factory, task_id)
        elif decision == "rollback":
            unit.rollback()
        elif decision == "cancel commit":
            unit.commit()
            unit.rollback()
        assert outcomes == []

    expected = "commit" if decision == "commit" else "rollback"
    assert outcomes == [expected]
    assert connection.closed
    assert not session.in_transaction()
    assert is_visible(factory, task_id) is (decision == "commit")


@pytest.mark.parametrize(
    "explicit_flush", [True, False], ids=["before-commit", "during-commit"]
)
def test_deadline_after_orm_flush_prevents_physical_commit(
    transaction_task: tuple[PostgresFactory, Task],
    monkeypatch: pytest.MonkeyPatch,
    explicit_flush: bool,
) -> None:
    factory, task = transaction_task
    task_id = task.task_id
    clock = SimpleNamespace(now=1000.0)
    fake_time = SimpleNamespace(time=lambda: clock.now, monotonic=lambda: clock.now)
    monkeypatch.setattr(budget, "time", fake_time)
    with budget.dispatch_budget(1001.0):
        with pytest.raises(TimeoutError, match="dispatch deadline exceeded"):
            with (
                factory.get_scoped_session() as session,
                SqlAlchemyUnitOfWork(session) as unit,
            ):
                session = unit.session
                connection, outcomes = observe_transaction(session)

                def expire_after_flush(*_args: object) -> None:
                    assert (
                        connection.scalar(
                            select(Task.task_id).where(Task.task_id == task_id)
                        )
                        == task_id
                    )
                    assert not is_visible(factory, task_id)
                    clock.now = 1002.0

                event.listen(session, "after_flush", expire_after_flush)
                session.add(task)
                if explicit_flush:
                    session.flush()
                unit.commit()

    assert outcomes == ["rollback"]
    assert connection.closed
    assert not session.in_transaction()
    assert not is_visible(factory, task_id)


@pytest.mark.parametrize(
    "failure", ["validation", "statement", "flush", "physical commit"]
)
def test_failures_rollback_and_close(
    transaction_task: tuple[PostgresFactory, Task], failure: str
) -> None:
    factory, task = transaction_task
    task_id = task.task_id
    error = ValueError if failure == "validation" else PostgresUnavailableError
    with pytest.raises(error) as exc:
        with (
            factory.get_scoped_session() as session,
            SqlAlchemyUnitOfWork(session) as unit,
        ):
            session = unit.session
            connection, outcomes = observe_transaction(session)
            session.add(task)
            unit.commit()
            if failure == "flush":
                task.status = "INVALID_STATUS"
            else:
                session.flush()
            if failure == "validation":
                raise ValueError("validation failed")
            if failure == "statement":
                session.execute(text("SELECT 1 / 0"))
            if failure == "physical commit":
                session.execute(
                    text("CREATE TEMP TABLE parent (id integer PRIMARY KEY)")
                )
                session.execute(
                    text(
                        "CREATE TEMP TABLE child (parent_id integer REFERENCES parent "
                        "DEFERRABLE INITIALLY DEFERRED)"
                    )
                )
                session.execute(text("INSERT INTO child VALUES (1)"))

    if failure == "physical commit":
        assert exc.value.__suppress_context__
        cause = exc.value.__context__
        assert isinstance(cause, DBAPIError)
        assert getattr(cause.orig, "sqlstate", None) == "23503"
        assert outcomes == ["commit"]
    else:
        assert outcomes == ["rollback"]
    assert connection.closed
    assert not session.in_transaction()
    assert not is_visible(factory, task_id)


@pytest.mark.parametrize("decision", ["commit", "unset", "failure"])
def test_ci_write_profile_preserves_settings_and_transaction_outcome(
    transaction_task: tuple[PostgresFactory, Task], decision: str
) -> None:
    factory, task = transaction_task
    task_id = task.task_id
    with factory.connect_session() as connection:
        # Deliberately differ from CI transaction-local settings.
        connection.execute(
            text(
                "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL SERIALIZABLE"
            )
        )
        connection.execute(text("SET lock_timeout = '1s'"))
        connection.execute(text("SET statement_timeout = '2s'"))
        connection.execute(text("SET synchronous_commit = off"))
        connection.commit()

        expected_error = (
            pytest.raises(ValueError, match="validation failed")
            if decision == "failure"
            else nullcontext()
        )
        with expected_error:
            with (
                factory.get_sync_session(connection) as session,
                SqlAlchemyUnitOfWork(session, profile="ci_write") as unit,
            ):
                _, outcomes = observe_transaction(session)
                assert (
                    session.scalar(text("SHOW transaction_isolation"))
                    == "read committed"
                )
                assert session.scalar(text("SHOW lock_timeout")) == "5s"
                assert session.scalar(text("SHOW statement_timeout")) == "5s"
                assert session.scalar(text("SHOW synchronous_commit")) == "on"
                session.add(task)
                session.flush()
                assert not is_visible(factory, task_id)
                if decision != "unset":
                    unit.commit()
                    assert not is_visible(factory, task_id)
                if decision == "failure":
                    raise ValueError("validation failed")
                assert outcomes == []

        assert outcomes == ["commit" if decision == "commit" else "rollback"]
        assert not session.in_transaction()
        assert not connection.in_transaction()
        assert connection.scalar(text("SHOW transaction_isolation")) == "serializable"
        assert connection.scalar(text("SHOW lock_timeout")) == "1s"
        assert connection.scalar(text("SHOW statement_timeout")) == "2s"
        assert connection.scalar(text("SHOW synchronous_commit")) == "off"

    assert connection.closed
    assert is_visible(factory, task_id) is (decision == "commit")
