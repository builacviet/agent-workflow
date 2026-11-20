"""Verify the local operator write slice on a disposable PostgreSQL database."""

import json
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

from agent_workflow import admin, budget
from agent_workflow.app_config import AppConfig
from agent_workflow.application.task.use_cases.authorize_task import (
    AuthorizeTaskCommand,
)
from agent_workflow.composition.task_deps import create_authorize_task_use_case
from agent_workflow.domain.service import WorkflowService, encode
from agent_workflow.domain.task.authorization import TaskAuthorizationSpec
from agent_workflow.domain.workflow_identity import (
    LEGACY_REPOSITORY,
    REPOSITORY,
    WorkflowError,
)
from agent_workflow.infrastructure.database.models import Task, TaskEvent
from agent_workflow.infrastructure.database.repositories.task_authorization_repository import (
    CorruptedPersistenceDataError,
    SqlAlchemyTaskAuthorizationRepository,
)
from agent_workflow.infrastructure.database.session_factory import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


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
            task_ids = (task_id, f"{task_id}-legacy")
            with factory.transaction() as connection:
                connection.execute(
                    delete(TaskEvent).where(TaskEvent.task_id.in_(task_ids))
                )
                connection.execute(delete(Task).where(Task.task_id.in_(task_ids)))


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


def test_operator_cli_readback_matches_legacy_and_preserves_history(
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
        ["agent_workflow.admin", "provision", "--file", str(path), "--postgres"],
    )
    monkeypatch.setattr(
        admin, "default_service", lambda: pytest.fail("legacy CLI fallback")
    )
    admin.main()

    task, events = read_rows(factory, spec.task_id)
    assert task["metadata"] == encode(payload)
    assert len(events) == 1
    assert task["updated_at"] == events[0]["timestamp"]
    legacy_id = f"{spec.task_id}-legacy"
    times = iter((task["created_at"], task["updated_at"]))
    legacy = WorkflowService(
        repository=PostgresWorkflowRepository(factory),
        require_ci=False,
        clock=lambda: next(times),
    )
    legacy.provision_task(payload | {"task_id": legacy_id})
    legacy_task, legacy_events = read_rows(factory, legacy_id)
    assert dict(task) == dict(legacy_task) | {
        "task_id": spec.task_id,
        "metadata": encode(payload),
    }
    assert dict(events[0]) == dict(legacy_events[0]) | {
        "task_id": spec.task_id,
        "event_id": events[0]["event_id"],
    }
    actual_context = legacy.get_review_context(spec.task_id)
    expected_context = legacy.get_review_context(legacy_id)
    assert actual_context == expected_context | {
        "task_id": spec.task_id,
        "events": actual_context["events"],
    }

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
    legacy.clock = lambda: task["updated_at"] + 1
    legacy.register_task_handoff(
        spec.task_id,
        REPOSITORY,
        spec.branch,
        spec.base_sha,
        "b" * 40,
        "docs/handoff.md",
        "Disposable test",
    )
    before = read_rows(factory, spec.task_id)
    authorize(factory, spec)
    assert read_rows(factory, spec.task_id) == before
    assert [row["action"] for row in before[1]][0] == "LOCAL_AUTHORIZATION"
    assert len(before[1]) == 2


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


@pytest.mark.parametrize(
    ("legacy_follower", "conflict"), [(False, False), (False, True), (True, False)]
)
def test_concurrent_create_collision(
    pg_authorization, monkeypatch, legacy_follower, conflict
):
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
        if legacy_follower:
            legacy = WorkflowService(repository=PostgresWorkflowRepository(factory))
            legacy.provision_task(json.loads(encode(asdict(other_spec))))
        else:
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
