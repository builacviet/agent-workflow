"""Verify the local operator write slice on a disposable PostgreSQL database."""

import json
import logging
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, replace
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, select, text, update

from agent_workflow import budget
from agent_workflow.app_config import AppConfig
from agent_workflow.application.shared.errors import CorruptedPersistenceDataError
from agent_workflow.application.task.use_cases import authorize_task
from agent_workflow.application.task.use_cases.authorize_task import (
    AuthorizeTaskCommand,
)
from agent_workflow.composition.task_deps import create_authorize_task_use_case
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.domain.task.authorization import TaskAuthorizationSpec
from agent_workflow.domain.task.handoff import HandoffRequest
from agent_workflow.domain.workflow_identity import (
    LEGACY_REPOSITORY,
    REPOSITORY,
    WorkflowError,
)
from agent_workflow.infrastructure.database.models import Task, TaskEvent
from agent_workflow.infrastructure.database.repositories.task_authorization_repository import (
    SqlAlchemyTaskAuthorizationRepository,
)
from agent_workflow.infrastructure.database.session_factory import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.observability.correlation import (
    correlation_scope,
    get_correlation_id,
    set_correlation_id,
)
from agent_workflow.interfaces.cli import admin


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@pytest.fixture
def pg_authorization():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    task_id = f"R2-DB-03-{uuid.uuid4().hex}"
    spec = TaskAuthorizationSpec(
        task_id=task_id,
        title="Résumé",
        description="  Keep space and original case  ",
        acceptance_criteria=("Persist one event", "Verify committed rows"),
        definition_of_done="Observed",
        repository=LEGACY_REPOSITORY,
        branch="codex/test",
        base_sha="a" * 40,
        requested_effort="high",
        risk_level="medium",
        context_paths=("AGENTS.md", "docs/handoff.md"),
        synthetic=True,
    )
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        try:
            yield factory, spec
        finally:
            with factory.transaction() as connection:
                connection.execute(
                    delete(TaskEvent).where(TaskEvent.task_id == task_id)
                )
                connection.execute(delete(Task).where(Task.task_id == task_id))


def read_rows(factory, task_id):
    with factory.connect_session() as connection:
        task = (
            connection.execute(select(Task.__table__).where(Task.task_id == task_id))
            .mappings()
            .one_or_none()
        )
        events = (
            connection.execute(
                select(TaskEvent.__table__)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.event_id)
            )
            .mappings()
            .all()
        )
    return task, events


def observe_scopes(factory, monkeypatch, *, before_execute=None):
    scopes = []
    open_session = factory.get_sync_session

    @contextmanager
    def record(connection):
        with open_session(connection) as session:
            outcomes = []
            event.listen(connection, "commit", lambda _: outcomes.append("commit"))
            event.listen(connection, "rollback", lambda _: outcomes.append("rollback"))
            if before_execute is not None:
                event.listen(connection, "before_cursor_execute", before_execute)
            scopes.append((connection, session, outcomes))
            yield session

    monkeypatch.setattr(factory, "get_sync_session", record)
    return scopes


def authorize(factory, spec):
    with create_authorize_task_use_case(factory) as use_case:
        use_case.execute(AuthorizeTaskCommand(spec))


def assert_closed(scopes):
    assert all(
        connection.closed and not session.in_transaction()
        for connection, session, _ in scopes
    )


def test_task_and_event_share_one_physical_commit(pg_authorization, monkeypatch):
    factory, spec = pg_authorization
    scopes = observe_scopes(factory, monkeypatch)
    add = SqlAlchemyTaskAuthorizationRepository.add

    def check_uncommitted(repository, authorization):
        add(repository, authorization)
        assert read_rows(factory, spec.task_id) == (None, [])
        connection, _, outcomes = scopes[0]
        assert (
            connection.scalar(select(Task.task_id).where(Task.task_id == spec.task_id))
            == spec.task_id
        )
        assert (
            connection.scalar(
                select(TaskEvent.action).where(TaskEvent.task_id == spec.task_id)
            )
            == "LOCAL_AUTHORIZATION"
        )
        assert outcomes == []

    monkeypatch.setattr(SqlAlchemyTaskAuthorizationRepository, "add", check_uncommitted)
    authorize(factory, spec)
    before = read_rows(factory, spec.task_id)
    authorize(factory, spec)
    with pytest.raises(WorkflowError, match="conflicts with stored metadata"):
        authorize(factory, replace(spec, risk_level="high"))

    assert read_rows(factory, spec.task_id) == before
    task, events = before
    assert task["status"] == "AWAITING_HANDOFF"
    assert [row["action"] for row in events] == ["LOCAL_AUTHORIZATION"]
    assert [outcomes for _, _, outcomes in scopes] == [
        ["commit"],
        ["rollback"],
        ["rollback"],
    ]
    assert_closed(scopes)


def test_operator_cli_readback_preserves_history(
    pg_authorization, monkeypatch, tmp_path
):
    factory, spec = pg_authorization
    payload = json.loads(encode(asdict(spec)))
    path = tmp_path / "task.json"
    path.write_text(
        encode(payload | {"repository": LEGACY_REPOSITORY}), encoding="utf-8"
    )
    monkeypatch.setenv("PG_RUNTIME_DSN", os.environ["PG_TEST_DSN"])
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent_workflow.interfaces.cli.admin", "provision", "--file", str(path)],
    )
    admin.main()

    task, events = read_rows(factory, spec.task_id)
    assert task["metadata"] == encode(payload)
    assert len(events) == 1
    assert task["updated_at"] == events[0]["timestamp"]
    with factory.transaction() as connection:
        # Simulate a pre-R2 row that keeps the historical repository slug.
        connection.execute(
            update(Task)
            .where(Task.task_id == spec.task_id)
            .values(
                metadata_text=encode(payload | {"repository": LEGACY_REPOSITORY}),
                repository=LEGACY_REPOSITORY,
            )
        )
    assert task["repository"] == REPOSITORY
    assert task["status"] == "AWAITING_HANDOFF"
    with PostgresHandoffProvider(
        factory, clock=lambda: task["updated_at"] + 1
    )() as operation:
        operation.execute(
            HandoffRequest(
                spec.task_id,
                REPOSITORY,
                spec.branch,
                spec.base_sha,
                "b" * 40,
                "docs/handoff.md",
                "Disposable test",
            )
        )
    before = read_rows(factory, spec.task_id)
    authorize(factory, spec)
    assert read_rows(factory, spec.task_id) == before
    assert [row["action"] for row in before[1]][0] == "LOCAL_AUTHORIZATION"
    assert len(before[1]) == 2


def test_live_cli_correlates_readiness_sql_and_logs_after_commit(
    pg_authorization, monkeypatch, tmp_path, capsys
):
    observer, spec = pg_authorization
    path = tmp_path / "task.json"
    path.write_text(json.dumps(asdict(spec)), encoding="utf-8")
    correlation_id = "live-cli-observation"
    monkeypatch.setenv("CORRELATION_ID", correlation_id)
    monkeypatch.setenv("PG_RUNTIME_DSN", os.environ["PG_TEST_DSN"])
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent_workflow.interfaces.cli.admin", "provision", "--file", str(path)],
    )

    readiness = PostgresFactory.assert_schema_ready
    statements = []

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def instrument_readiness(factory):
        event.listen(factory._engine, "before_cursor_execute", observe_sql)
        readiness(factory)

    monkeypatch.setattr(PostgresFactory, "assert_schema_ready", instrument_readiness)
    logger = logging.getLogger(authorize_task.__name__)
    info = logger.info
    visible_at_success = []

    def observe_log(event_name):
        if event_name == "task_authorization_committed":
            task, events = read_rows(observer, spec.task_id)
            visible_at_success.append(
                (task["task_id"], [row["action"] for row in events])
            )
        info(event_name)

    monkeypatch.setattr(logger, "info", observe_log)

    for result in ("committed", "skipped"):
        statements.clear()
        admin.main()
        assert get_correlation_id() is None
        prefix = f"/* correlation_id={correlation_id} */ "
        assert statements[0] == prefix + "SELECT version_num FROM alembic_version"
        assert any("pg_advisory_xact_lock" in sql for sql in statements)
        assert all(
            sql.startswith(prefix) and sql.count(prefix) == 1 for sql in statements
        )
        assert any("INSERT INTO tasks" in sql for sql in statements) == (
            result == "committed"
        )
        stderr = capsys.readouterr().err
        assert spec.task_id not in stderr
        assert spec.description.strip() not in stderr
        logs = [json.loads(line) for line in stderr.splitlines()]
        assert [record["event"] for record in logs] == [
            "task_authorization_started",
            f"task_authorization_{result}",
        ]
        assert {record["correlation_id"] for record in logs} == {correlation_id}
    assert visible_at_success == [(spec.task_id, ["LOCAL_AUTHORIZATION"])]


@pytest.mark.parametrize("correlation_id", ["valid-DB_1", "bad*/id", "bad\nid", None])
def test_live_postgres_receives_safe_comment_and_unchanged_parameter(
    pg_authorization, correlation_id
):
    factory, _ = pg_authorization
    probe = "literal input */ not part of SQL"
    with correlation_scope(), factory.connect_session() as connection:
        set_correlation_id(correlation_id)
        sql, bound = connection.execute(
            text(
                "/* correlation_id=prior */ "
                "SELECT current_query(), CAST(:probe AS text)"
            ),
            {"probe": probe},
        ).one()
    assert bound == probe
    assert probe not in sql
    assert "correlation_id=prior" not in sql
    assert sql.count("correlation_id=") == (1 if correlation_id == "valid-DB_1" else 0)
    if correlation_id == "valid-DB_1":
        assert sql.startswith("/* correlation_id=valid-DB_1 */ SELECT")
    else:
        assert sql.startswith("SELECT")


def test_live_postgres_commit_rejection_rolls_back_without_success(
    pg_authorization, monkeypatch, caplog
):
    factory, spec = pg_authorization
    caplog.set_level(logging.INFO, logger=authorize_task.__name__)
    scopes = observe_scopes(factory, monkeypatch)
    add = SqlAlchemyTaskAuthorizationRepository.add

    def defer_violation_until_commit(repository, authorization):
        add(repository, authorization)
        connection, _, outcomes = scopes[0]
        connection.exec_driver_sql(
            "CREATE TEMP TABLE deferred_violation "
            "(value int UNIQUE DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"
        )
        connection.exec_driver_sql("INSERT INTO deferred_violation VALUES (1), (1)")
        assert outcomes == []

    monkeypatch.setattr(
        SqlAlchemyTaskAuthorizationRepository, "add", defer_violation_until_commit
    )
    with pytest.raises(
        PostgresUnavailableError, match="^PostgreSQL unit of work failed$"
    ):
        authorize(factory, spec)
    assert scopes[0][2] == ["commit"]
    assert_closed(scopes)
    assert read_rows(factory, spec.task_id) == (None, [])
    assert caplog.messages == ["task_authorization_started"]


@pytest.mark.parametrize("failure", ["exception", "database", "deadline"])
def test_failure_after_writes_rolls_back_task_and_event(
    pg_authorization, monkeypatch, failure
):
    factory, spec = pg_authorization
    scopes = observe_scopes(factory, monkeypatch)
    add = SqlAlchemyTaskAuthorizationRepository.add
    timer = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(
        budget,
        "time",
        SimpleNamespace(time=lambda: timer.now, monotonic=lambda: timer.now),
    )

    def fail_after_write(repository, authorization):
        add(repository, authorization)
        if failure == "exception":
            raise RuntimeError("application exception")
        if failure == "database":
            scopes[0][0].execute(text("SELECT 1 / 0"))
        timer.now = 1002.0

    monkeypatch.setattr(SqlAlchemyTaskAuthorizationRepository, "add", fail_after_write)
    errors = {
        "exception": (RuntimeError, "application exception"),
        "database": (
            PostgresUnavailableError,
            "^PostgreSQL unit of work failed$",
        ),
        "deadline": (TimeoutError, "dispatch deadline exceeded"),
    }
    error, message = errors[failure]
    limit = budget.dispatch_budget(1001.0) if failure == "deadline" else nullcontext()
    with limit, pytest.raises(error, match=message):
        authorize(factory, spec)
    assert [outcomes for _, _, outcomes in scopes] == [["rollback"]]
    assert_closed(scopes)
    assert read_rows(factory, spec.task_id) == (None, [])


@pytest.mark.parametrize("conflict", [False, True])
def test_concurrent_create_collision(pg_authorization, monkeypatch, conflict):
    factory, spec = pg_authorization
    first_written, follower_query, finish = Event(), Event(), Event()
    backends = []
    add = SqlAlchemyTaskAuthorizationRepository.add

    def hold_first(repository, authorization):
        add(repository, authorization)
        first_written.set()
        assert finish.wait(10), "first writer did not receive release signal"

    def identify_follower(connection, _cursor, statement, *_):
        if first_written.is_set() and "pg_advisory_xact_lock" in statement:
            backends.append(connection.connection.driver_connection.info.backend_pid)
            follower_query.set()

    monkeypatch.setattr(SqlAlchemyTaskAuthorizationRepository, "add", hold_first)
    scopes = observe_scopes(factory, monkeypatch, before_execute=identify_follower)
    other_spec = replace(spec, title="Different spec") if conflict else spec

    def follow():
        authorize(factory, other_spec)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(authorize, factory, spec)
        try:
            assert first_written.wait(5), "first request did not write"
            second = pool.submit(follow)
            assert follower_query.wait(5), "follower did not attempt advisory lock"
            with factory.connect_session() as observer:
                lock_query = text("SELECT cardinality(pg_blocking_pids(:pid))")
                deadline = time.monotonic() + 4
                while not observer.scalar(lock_query, {"pid": backends[0]}):
                    assert time.monotonic() < deadline, (
                        "follower never waited for the winner"
                    )
                    time.sleep(0.01)
            assert read_rows(factory, spec.task_id) == (None, [])
        finally:
            finish.set()
        first.result(timeout=5)
        if conflict:
            with pytest.raises(WorkflowError, match="conflicts with stored metadata"):
                second.result(timeout=5)
        else:
            second.result(timeout=5)

    task, events = read_rows(factory, spec.task_id)
    assert task["metadata"] == encode(asdict(spec))
    assert [row["action"] for row in events] == ["LOCAL_AUTHORIZATION"]
    assert_closed(scopes)


@pytest.mark.parametrize("metadata", ["{", '{"task_id":"wrong"}'])
def test_corrupt_persisted_spec_fails_separately(pg_authorization, metadata):
    factory, spec = pg_authorization
    authorize(factory, spec)
    with factory.transaction() as connection:
        connection.execute(
            update(Task)
            .where(Task.task_id == spec.task_id)
            .values(metadata_text=metadata)
        )
    before = read_rows(factory, spec.task_id)
    with pytest.raises(
        CorruptedPersistenceDataError, match="Invalid persisted task authorization"
    ):
        authorize(factory, spec)
    assert read_rows(factory, spec.task_id) == before


@pytest.mark.parametrize(
    "updates",
    [
        {"task_id": "bad\0task"},
        {"title": "bad\0title"},
        {"description": "\ud800"},
        {"context_paths": ("AGENTS.md", "bad\0path")},
    ],
)
def test_direct_use_case_rejects_unsafe_storage_text_and_rolls_back(
    pg_authorization, monkeypatch, updates
):
    factory, spec = pg_authorization
    scopes = observe_scopes(factory, monkeypatch)
    unsafe = replace(spec, **updates)

    with pytest.raises(WorkflowError, match="NUL|UTF-8"):
        authorize(factory, unsafe)

    assert [outcomes for _, _, outcomes in scopes] == [["rollback"]]
    assert_closed(scopes)
    assert read_rows(factory, spec.task_id) == (None, [])


def test_persisted_unicode_corruption_is_distinct_from_db_unavailability(
    pg_authorization,
):
    factory, spec = pg_authorization
    authorize(factory, spec)

    with factory.transaction() as connection:
        original = json.loads(
            connection.scalar(
                select(Task.metadata_text).where(Task.task_id == spec.task_id)
            )
        )
        original["description"] = "\ud800"
        connection.execute(
            update(Task)
            .where(Task.task_id == spec.task_id)
            .values(metadata_text=json.dumps(original))
        )

    with pytest.raises(
        CorruptedPersistenceDataError, match="Invalid persisted task authorization"
    ):
        authorize(factory, spec)
