"""Live PostgreSQL workflow contract checks on a disposable database."""

import asyncio
import json
import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError
from pg_task_fixtures import authorize_task, record_handoff
from sqlalchemy import event, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_agent_workflow import BASE, HEAD, NEXT, REPO, payload, spec

from agent_workflow import budget
from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import PersistenceUnavailableError
from agent_workflow.application.review.use_cases.claim_review import ClaimReviewUseCase
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
)
from agent_workflow.application.review.use_cases.submit_review import (
    SubmitReviewUseCase,
)
from agent_workflow.application.review.use_cases.write_contracts import ClaimReviewInput
from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.postgres_mcp import (
    postgres_mcp_review_queue,
    postgres_mcp_review_writes,
    postgres_mcp_task_operations,
)
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.infrastructure.database.models import (
    CITask,
    Review,
    ReviewAttempt,
    ReviewFinding,
    Task,
    TaskEvent,
)
from agent_workflow.infrastructure.database.readers.review_queue_reader import (
    PostgresReviewQueueReader,
)
from agent_workflow.infrastructure.database.repositories.review_lease_repository import (
    PostgresReviewLeaseRepository,
)
from agent_workflow.infrastructure.database.repositories.review_writes import (
    PostgresReviewWrites,
)
from agent_workflow.infrastructure.database.repositories.task_handoff import (
    PostgresHandoffRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.interfaces.mcp.review_request import Submission
from agent_workflow.interfaces.review_assignment_response import review_context_response
from agent_workflow.mcp.server import mcp


@pytest.mark.parametrize("require_ci", [True, False])
def test_mcp_composition_owns_factory_and_config(require_ci):
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test",
        ci_gate_required=require_ci,
    )
    factory = Mock()
    with patch.object(PostgresFactory, "open", return_value=factory) as open_factory:
        with postgres_mcp_task_operations(config) as (handoff, assignment):
            open_factory.assert_called_once_with(config)
            assert isinstance(handoff, PostgresHandoffProvider)
            assert handoff.factory is factory
            factory.get_scoped_session.assert_not_called()
            assert assignment.context is not None
            factory.close.assert_not_called()
        factory.close.assert_called_once_with()


def test_mcp_composition_closes_on_scope_and_construction_errors():
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test"
    )
    factory = Mock()
    with patch.object(PostgresFactory, "open", return_value=factory):
        with pytest.raises(RuntimeError, match="scope error"):
            with postgres_mcp_task_operations(config):
                raise RuntimeError("scope error")
        factory.close.assert_called_once_with()
        factory.reset_mock()
        with patch(
            "agent_workflow.composition.postgres_mcp.PostgresHandoffProvider",
            side_effect=RuntimeError("construction error"),
        ):
            with pytest.raises(RuntimeError, match="construction error"):
                with postgres_mcp_task_operations(config):
                    pytest.fail("service must not be yielded")
        factory.close.assert_called_once_with()


def test_review_write_composition_shares_one_fresh_scoped_session_per_invocation():
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test",
        ci_gate_required=True,
    )
    factories = [MagicMock(spec=PostgresFactory) for _ in range(2)]
    sessions = [Mock(), Mock()]
    for factory, session in zip(factories, sessions, strict=True):
        factory.__enter__.return_value = factory
        factory.get_scoped_session.return_value.__enter__.return_value = session

    with patch.object(PostgresFactory, "open", side_effect=factories) as open_factory:
        with postgres_mcp_review_writes(config) as first:
            claim, submit = first
            assert claim.uow is submit.uow
            assert claim.repository is submit.repository
            assert claim.uow._session is claim.repository._session is sessions[0]
            assert claim.require_ci is submit.require_ci is True
            assert sessions[0].mock_calls == []
        with pytest.raises(RuntimeError, match="tool failed"):
            with postgres_mcp_review_writes(config) as (claim, submit):
                assert claim.uow is submit.uow and claim.uow is not first[0].uow
                assert claim.repository is submit.repository
                assert claim.repository is not first[0].repository
                assert claim.uow._session is claim.repository._session is sessions[1]
                raise RuntimeError("tool failed")

    assert open_factory.call_count == 2
    for factory in factories:
        factory.get_scoped_session.assert_called_once_with()
        factory.get_scoped_session.return_value.__exit__.assert_called_once()
        factory.__exit__.assert_called_once()


def test_queue_composition_shares_one_fresh_session_per_invocation():
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test",
        ci_gate_required=True,
    )
    factory = Mock()
    first_scope, second_scope = MagicMock(), MagicMock()
    first_session, second_session = Mock(), Mock()
    first_scope.__enter__.return_value = first_session
    second_scope.__enter__.return_value = second_session
    factory.get_scoped_session.side_effect = [first_scope, second_scope]
    with patch.object(PostgresFactory, "open", return_value=factory) as open_factory:
        with postgres_mcp_review_queue(config) as first:
            assert first._uow._session is first._leases._session is first_session
            assert first._reader._session is first_session
            assert first_session.mock_calls == []
        with pytest.raises(RuntimeError, match="tool failed"):
            with postgres_mcp_review_queue(config) as second:
                assert second._uow._session is second._leases._session is second_session
                assert second._reader._session is second_session
                raise RuntimeError("tool failed")
    assert open_factory.call_count == 2
    assert factory.get_scoped_session.call_count == 2
    first_scope.__exit__.assert_called_once()
    second_scope.__exit__.assert_called_once()
    assert factory.close.call_count == 2


@pytest.mark.parametrize("dsn", [None, "sqlite:///fallback.db", "invalid"])
def test_mcp_composition_rejects_missing_or_invalid_dsn(dsn):
    config = AppConfig.model_construct(pg_runtime_dsn=dsn)
    with patch(
        "agent_workflow.infrastructure.database.session_factory.create_engine"
    ) as engine:
        with pytest.raises(ValueError, match="PG_RUNTIME_DSN"):
            with postgres_mcp_task_operations(config):
                pytest.fail("operation must not be yielded")
        engine.assert_not_called()


def clean_test_tasks(factory, task_ids):
    with factory.transaction() as conn:
        for table in (
            "review_findings",
            "reviews",
            "review_attempts",
            "task_events",
            "controller_ci_observations",
            "controller_ci_tasks",
            "tasks",
        ):
            conn.execute(
                text(f"DELETE FROM {table} WHERE task_id = ANY(:task_ids)"),
                {"task_ids": task_ids},
            )


@pytest.fixture
def setup():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    task_id = "PG02B-" + uuid.uuid4().hex
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        try:
            yield factory, task_id
        finally:
            clean_test_tasks(factory, [task_id])


@pytest.fixture
def queue_tasks(setup):
    factory, task_id = setup
    extra_ids = [f"{task_id}-{label}" for label in ("a", "m", "z")]
    try:
        yield extra_ids
    finally:
        clean_test_tasks(factory, extra_ids)


# Detached-safe scalar row snapshots for independent persisted-state assertions.
def task_row(session: Session, task_id: str):
    return session.execute(
        select(Task.__table__).where(Task.task_id == task_id)
    ).one_or_none()


def active_claim_row(session: Session, task_id: str):
    return session.execute(
        select(ReviewAttempt.__table__).where(
            ReviewAttempt.task_id == task_id, ReviewAttempt.status == "ACTIVE"
        )
    ).one_or_none()


def claim_row(session: Session, token: str, task_id: str):
    return session.execute(
        select(ReviewAttempt.__table__).where(
            ReviewAttempt.token == token, ReviewAttempt.task_id == task_id
        )
    ).one_or_none()


def event_rows(session: Session, task_id: str):
    return session.execute(
        select(TaskEvent.__table__)
        .where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.event_id)
    ).all()


def review_ids(session: Session, task_id: str) -> list[str]:
    return list(
        session.scalars(
            select(Review.review_id)
            .where(Review.task_id == task_id)
            .order_by(Review.round_number)
        )
    )


def ready(factory, task_id):
    authorize_task(factory, spec(task_id))
    record_handoff(
        factory, task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "test"
    )


def claim_task(
    factory, task_id, head, reviewer, *, require_ci=False, clock=None, lease=900
):
    with factory.get_scoped_session() as session:
        operation = ClaimReviewUseCase(
            SqlAlchemyUnitOfWork(session),
            PostgresReviewWrites(session),
            clock=clock or (lambda: 1000.0),
            token=lambda: uuid.uuid4().hex,
            require_ci=require_ci,
            lease_seconds=lease,
        )
        return asdict(operation.execute(ClaimReviewInput(task_id, head, reviewer)))


def submit_task(factory, data, reviewer, *, clock=None, require_ci=False):
    with factory.get_scoped_session() as session:
        operation = SubmitReviewUseCase(
            SqlAlchemyUnitOfWork(session),
            PostgresReviewWrites(session),
            clock=clock or (lambda: 1000.0),
            token=lambda: uuid.uuid4().hex,
            require_ci=require_ci,
        )
        return asdict(
            operation.execute(Submission.model_validate(data).to_input(reviewer))
        )


def read_context(factory, task_id):
    operation = create_get_review_context_use_case(factory)
    return review_context_response(operation.execute(GetReviewContextInput(task_id)))


@pytest.fixture
def mcp_postgres(setup, monkeypatch):
    monkeypatch.setenv("PG_RUNTIME_DSN", os.environ["PG_TEST_DSN"])
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    monkeypatch.setenv("WFC_AUTH_MODE", "auth0")
    monkeypatch.setenv("WFC_AUTH0_ALLOWED_SUB", "auth0|pg-test")
    monkeypatch.setenv("WFC_AUTH0_SCOPE", "wfc:workflow")

    @contextmanager
    def authorize():
        token = AccessToken(
            token="synthetic",
            client_id="test",
            scopes=["wfc:workflow"],
            subject="auth0|pg-test",
        )
        reset = auth_context_var.set(AuthenticatedUser(token))
        try:
            yield
        finally:
            auth_context_var.reset(reset)

    def invoke(name, arguments):
        with authorize():
            result = asyncio.run(mcp.call_tool(name, arguments))
        return json.loads(result[0].text)

    return invoke


def test_mcp_handoff_persists_across_tool_invocations(setup, mcp_postgres):
    factory, task_id = setup
    authorize_task(factory, spec(task_id))
    assert read_context(factory, task_id)["current_head_sha"] is None
    handoff = mcp_postgres(
        "register_task_handoff",
        {
            "task_id": task_id,
            "repository": REPO,
            "branch": "task/test",
            "base_sha": BASE,
            "new_head_sha": HEAD,
            "handoff_file_path": "docs/handoff.md",
            "test_summary": "PostgreSQL MCP fixture",
        },
    )
    context = mcp_postgres("get_review_context", {"task_id": task_id})
    assert handoff == {
        "task_id": task_id,
        "status": "READY_IN_REVIEW",
        "head_sha": HEAD,
    }
    assert context["task_id"] == task_id
    assert context["current_head_sha"] == HEAD
    assert context["status"] == "READY_IN_REVIEW"
    assert context["events"][-1]["action"] == "HANDOFF"
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert session.scalar(
            select(TaskEvent.metadata_text)
            .where(
                TaskEvent.task_id == task_id,
                TaskEvent.sha == HEAD,
                TaskEvent.action.in_(("HANDOFF", "CI_REPAIR_HANDOFF")),
            )
            .order_by(TaskEvent.event_id.desc())
            .limit(1)
        ) == (
            '{"handoff_file_path":"docs/handoff.md",'
            '"test_summary":"PostgreSQL MCP fixture",'
            '"verification":"reporter_supplied_not_remotely_verified"}'
        )
    assert context["github_references"]["complete_diff"].endswith(BASE + "..." + HEAD)


def test_mcp_handoff_idempotent_and_empty_assignment(setup, mcp_postgres):
    factory, task_id = setup
    authorize_task(factory, spec(task_id))
    arguments = {
        "task_id": task_id,
        "repository": REPO,
        "branch": "task/test",
        "base_sha": BASE,
        "new_head_sha": HEAD,
        "handoff_file_path": "docs/handoff.md",
        "test_summary": "fixture",
    }
    assert mcp_postgres("register_task_handoff", arguments)["head_sha"] == HEAD
    assert mcp_postgres("register_task_handoff", arguments)["head_sha"] == HEAD
    assert (
        mcp_postgres("get_coder_assignment", {"task_id": task_id})[
            "unresolved_findings"
        ]
        == []
    )
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert sum(e.action == "HANDOFF" for e in event_rows(session, task_id)) == 1


def test_mcp_assignment_preserves_unresolved_findings(setup, mcp_postgres):
    factory, task_id = setup
    ready(factory, task_id)
    claim = claim_task(factory, task_id, HEAD, "lead")
    submit_task(factory, payload(claim, "CHANGES_REQUESTED"), "lead")
    actual = mcp_postgres("get_coder_assignment", {"task_id": task_id})
    assert actual["status"] == "CHANGES_REQUESTED"
    assert actual["current_head_sha"] == HEAD
    assert len(actual["unresolved_findings"]) == 1
    assert actual["unresolved_findings"][0]["finding_id"] == "F-1"
    assert actual["unresolved_findings"][0]["resolution_status"] == "NOT_VERIFIED"


def test_handoff_write_failure_rolls_back_head_and_event(setup, monkeypatch):
    factory, task_id = setup
    authorize_task(factory, spec(task_id))
    original = PostgresHandoffRepository.record

    def fail_after_writes(self, *args):
        original(self, *args)
        raise RuntimeError("synthetic write failure")

    monkeypatch.setattr(PostgresHandoffRepository, "record", fail_after_writes)
    with pytest.raises(RuntimeError, match="synthetic write failure"):
        record_handoff(
            factory,
            task_id,
            REPO,
            "task/test",
            BASE,
            HEAD,
            "docs/handoff.md",
            "fixture",
        )
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        task = task_row(session, task_id)
        assert task.current_head_sha is None
        assert task.status == "AWAITING_HANDOFF"
        assert [event.action for event in event_rows(session, task_id)] == [
            "LOCAL_AUTHORIZATION"
        ]


@pytest.mark.parametrize("verdict", ["APPROVED", "ESCALATED"])
def test_mcp_context_and_result_preserve_persisted_history(
    setup, mcp_postgres, verdict
):
    factory, task_id = setup
    arguments = {"task_id": task_id}
    authorize_task(
        factory, {**spec(task_id), "context_paths": ["docs/review context.md"]}
    )

    def assert_history(status, head, rounds):
        context = mcp_postgres("get_review_context", arguments)
        result = mcp_postgres("get_review_result", arguments)
        assert context["status"] == status
        assert context["current_head_sha"] == head
        assert context["completed_review_rounds"] == rounds
        assert len(context["previous_reviews"]) == rounds
        assert result["status"] == status
        assert result["previous_reviews"] == context["previous_reviews"]
        assert result["latest_reviewed_sha"] == (head if rounds else None)
        assert result["approval_current"] is None
        assert result["remote_verification"] == "not_performed_by_wfc"
        if head:
            assert context["github_references"]["context_files"] == {
                "docs/review context.md": f"https://github.com/{REPO}/blob/{head}/docs/review%20context.md"
            }

    assert_history("AWAITING_HANDOFF", None, 0)
    for sha, outcome, resolutions in (
        (HEAD, "CHANGES_REQUESTED", {}),
        (NEXT, verdict, {"F-1": "FIXED" if verdict == "APPROVED" else "NOT_FIXED"}),
    ):
        record_handoff(
            factory,
            task_id,
            REPO,
            "task/test",
            BASE,
            sha,
            "docs/review context.md",
            "test",
        )
        claim = claim_task(factory, task_id, sha, "lead")
        submit_task(
            factory,
            payload(
                claim,
                outcome,
                prior_resolutions=resolutions,
                **({"findings": []} if sha == NEXT else {}),
            ),
            "lead",
        )
        assert_history(outcome, sha, 1 if sha == HEAD else 2)

    for name in ("get_review_context", "get_review_result"):
        with pytest.raises(ToolError, match="unknown authorized task"):
            mcp_postgres(name, {"task_id": f"{task_id}-missing"})


def test_mcp_queue_filters_and_recovers_a_lease_in_the_workflow_transaction(
    setup,
    mcp_postgres,
):
    factory, task_id = setup
    ready(factory, task_id)
    claim = claim_task(factory, task_id, HEAD, "lead")
    assert read_context(factory, task_id)["status"] == "IN_REVIEW"

    # Even an effort filter with no matches recovers stale leases.
    assert mcp_postgres("list_review_queue", {"requested_effort": "invalid"}) == {
        "tasks": []
    }
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert claim_row(session, claim["claim_token"], task_id).status == "EXPIRED"
    assert read_context(factory, task_id)["events"][-1]["action"] == "LEASE_EXPIRED"

    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with factory.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO controller_ci_tasks (task_id, pr_number, head_sha, verdict)"
                " VALUES (:id, 7, :sha, 'SUCCESS')"
            ),
            {"id": task_id, "sha": NEXT},
        )
    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with factory.transaction() as conn:
        conn.execute(
            text("UPDATE controller_ci_tasks SET head_sha=:sha WHERE task_id=:id"),
            {"id": task_id, "sha": HEAD},
        )
    assert mcp_postgres("list_review_queue", {"requested_effort": "high"}) == {
        "tasks": []
    }
    assert mcp_postgres("list_review_queue", {"requested_effort": "medium"}) == {
        "tasks": [
            {
                "task_id": task_id,
                "title": spec(task_id)["title"],
                "repository": REPO,
                "branch": "task/test",
                "head_sha": HEAD,
                "requested_effort": "medium",
                "review_round": 1,
            }
        ]
    }


def test_mcp_queue_read_failure_rolls_back_lease_recovery(
    setup, mcp_postgres, monkeypatch
):
    factory, task_id = setup
    ready(factory, task_id)
    claim = claim_task(factory, task_id, HEAD, "lead")

    def fail_read(self, requested_effort):
        raise RuntimeError("synthetic queue read failure")

    monkeypatch.setattr(PostgresReviewQueueReader, "read", fail_read)
    with pytest.raises(ToolError, match="synthetic queue read failure"):
        mcp_postgres("list_review_queue", {})

    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert claim_row(session, claim["claim_token"], task_id).status == "ACTIVE"
    context = read_context(factory, task_id)
    assert context["status"] == "IN_REVIEW"
    assert context["events"][-1]["action"] == "CLAIM"


def test_queue_expiry_boundary_missing_claim_and_order(
    setup, queue_tasks, mcp_postgres, monkeypatch
):
    factory, task_id = setup
    null_head_id, nonexpired_id, missing_claim_id = queue_tasks
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    monkeypatch.setattr("agent_workflow.composition.postgres_mcp.time", lambda: 1000.0)
    for candidate in (missing_claim_id, nonexpired_id, null_head_id, task_id):
        ready(factory, candidate)
    expired = claim_task(factory, task_id, HEAD, "lead", clock=lambda: 990.0, lease=10)
    active = claim_task(
        factory, nonexpired_id, HEAD, "lead", clock=lambda: 991.0, lease=10
    )
    with factory.transaction() as connection:
        connection.execute(
            text("UPDATE tasks SET status='IN_REVIEW' WHERE task_id=:task_id"),
            {"task_id": missing_claim_id},
        )
        connection.execute(
            text("UPDATE tasks SET current_head_sha=NULL WHERE task_id=:task_id"),
            {"task_id": null_head_id},
        )
    with factory.transaction() as connection:
        connection.execute(
            insert(TaskEvent.__table__).values(
                task_id=null_head_id,
                previous_status="READY_IN_REVIEW",
                next_status="READY_IN_REVIEW",
                action="CI_REPAIR_HANDOFF",
                sha=None,
                timestamp=1000.0,
                metadata="{}",
            )
        )

    result = mcp_postgres("list_review_queue", {"requested_effort": "medium"})["tasks"]
    assert [(item["task_id"], item["head_sha"]) for item in result] == [
        (task_id, HEAD),
        (null_head_id, None),
    ]
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert claim_row(session, expired["claim_token"], task_id).status == "EXPIRED"
        assert active_claim_row(session, nonexpired_id).token == active["claim_token"]
        assert task_row(session, missing_claim_id).status == "IN_REVIEW"
        assert active_claim_row(session, missing_claim_id) is None
        event_row = event_rows(session, task_id)[-1]
        assert event_row.action == "LEASE_EXPIRED"
        assert event_row.timestamp == 1000.0


def test_queue_reader_only_reads_and_uses_latest_matching_handoff(setup):
    factory, task_id = setup
    ready(factory, task_id)
    with factory.transaction() as connection:
        for action, sha in (
            ("CI_REPAIR_HANDOFF", HEAD),
            ("HANDOFF", NEXT),
            ("CI_REPAIR_HANDOFF", None),
            ("CLAIM", HEAD),
        ):
            connection.execute(
                insert(TaskEvent.__table__).values(
                    task_id=task_id,
                    previous_status="READY_IN_REVIEW",
                    next_status="READY_IN_REVIEW",
                    action=action,
                    sha=sha,
                    timestamp=1000.0,
                    metadata="{}",
                )
            )
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        reader = PostgresReviewQueueReader(session)
        assert [item.task_id for item in reader.read(None)] == [task_id]
        assert reader.latest_head_handoff_action(task_id, HEAD) == "CI_REPAIR_HANDOFF"
        assert reader.latest_head_handoff_action(task_id, NEXT) == "HANDOFF"
        assert reader.latest_head_handoff_action(task_id, None) is None
        assert not reader.has_successful_ci(task_id, None)


def test_queue_projection_corruption_rolls_back_recovery_and_allows_retry(
    setup, mcp_postgres, monkeypatch
):
    factory, task_id = setup
    ready(factory, task_id)
    claim = claim_task(factory, task_id, HEAD, "lead")
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        original_task = task_row(session, task_id)
        original_events = event_rows(session, task_id)

    def store_metadata(value):
        with factory.transaction() as connection:
            connection.execute(
                text("UPDATE tasks SET metadata=:value WHERE task_id=:task_id"),
                {"task_id": task_id, "value": value},
            )

    store_metadata('{"title": {"corrupt": "private-canary"}}')
    with pytest.raises(ToolError, match="invalid persisted review queue task") as error:
        mcp_postgres("list_review_queue", {})
    assert "private-canary" not in str(error.value)
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert active_claim_row(session, task_id).token == claim["claim_token"]
        assert task_row(session, task_id).status == original_task.status
        assert event_rows(session, task_id) == original_events

    store_metadata(original_task.metadata)
    result = mcp_postgres("list_review_queue", {})["tasks"]
    assert [item["task_id"] for item in result] == [task_id]


def test_queue_ci_query_failure_rolls_back_savepoint_and_still_recovers(
    setup, mcp_postgres, monkeypatch
):
    factory, task_id = setup
    ready(factory, task_id)
    claim = claim_task(factory, task_id, HEAD, "lead")
    with factory.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO controller_ci_tasks (task_id, pr_number, head_sha, verdict)"
                " VALUES (:id, 7, :sha, 'SUCCESS')"
            ),
            {"id": task_id, "sha": HEAD},
        )
    original = PostgresReviewQueueReader.has_successful_ci

    def check_with_unavailable_ci(self, task_id, sha):
        def fail_query(state):
            if state.statement.column_descriptions[0]["entity"] is CITask:
                state.session.connection().execute(text("SELECT 1 / 0"))

        event.listen(self._session, "do_orm_execute", fail_query)
        try:
            result = original(self, task_id, sha)
            assert not self._session.in_nested_transaction()
            return result
        finally:
            event.remove(self._session, "do_orm_execute", fail_query)

    with monkeypatch.context() as patch:
        patch.setattr(
            PostgresReviewQueueReader, "has_successful_ci", check_with_unavailable_ci
        )
        assert mcp_postgres("list_review_queue", {}) == {"tasks": []}

    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert claim_row(session, claim["claim_token"], task_id).status == "EXPIRED"
        assert task_row(session, task_id).status == "READY_IN_REVIEW"
    result = mcp_postgres("list_review_queue", {})["tasks"]
    assert [item["task_id"] for item in result] == [task_id]


@pytest.mark.parametrize("operation", ["claim", "submit"])
def test_queue_holds_task_lock_against_competing_review_write(
    setup, mcp_postgres, monkeypatch, operation
):
    factory, task_id = setup
    ready(factory, task_id)
    expired = claim_task(factory, task_id, HEAD, "lead", clock=lambda: 990.0, lease=10)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    monkeypatch.setattr("agent_workflow.composition.postgres_mcp.time", lambda: 1000.0)
    locked, release = Event(), Event()
    writer_pid = Future()
    lock_queue = PostgresReviewLeaseRepository.lock_claimed_tasks
    lock_task = PostgresReviewWrites.lock_task

    def lock_then_wait(self):
        tasks = lock_queue(self)
        locked.set()
        assert release.wait(timeout=10), "queue lock was not released"
        return tasks

    def report_writer_pid(self, task_id):
        writer_pid.set_result(
            self._session.connection().scalar(text("SELECT pg_backend_pid()"))
        )
        return lock_task(self, task_id)

    def compete():
        if operation == "claim":
            return claim_task(factory, task_id, HEAD, "next")
        with pytest.raises(WorkflowError, match="claim expired"):
            submit_task(factory, payload(expired), "lead", clock=lambda: 999.0)
        return None

    monkeypatch.setattr(
        PostgresReviewLeaseRepository, "lock_claimed_tasks", lock_then_wait
    )
    monkeypatch.setattr(PostgresReviewWrites, "lock_task", report_writer_pid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        queued = pool.submit(mcp_postgres, "list_review_queue", {})
        try:
            assert locked.wait(timeout=5), "queue never took the task lock"
            writer = pool.submit(compete)
            pid = writer_pid.result(timeout=5)
            deadline = time.monotonic() + 3
            with factory.transaction() as connection:
                while not connection.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": pid}
                ):
                    assert time.monotonic() < deadline, (
                        "competing write never waited for the lock"
                    )
                    time.sleep(0.01)
        finally:
            release.set()
        assert [item["task_id"] for item in queued.result(timeout=5)["tasks"]] == [
            task_id
        ]
        competing_claim = writer.result(timeout=5)

    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert claim_row(session, expired["claim_token"], task_id).status == "EXPIRED"
        assert task_row(session, task_id).completed_review_rounds == 0
        assert review_ids(session, task_id) == []
        assert [row.action for row in event_rows(session, task_id)].count(
            "LEASE_EXPIRED"
        ) == 1
        active = active_claim_row(session, task_id)
        if competing_claim is None:
            assert active is None
        else:
            assert active.token == competing_claim["claim_token"]
            assert competing_claim["review_round"] == 1


@pytest.mark.parametrize(
    "checkpoint", ["before-session-commit", "after-session-commit", "no-recovery"]
)
def test_queue_deadline_guards_rollback_and_allows_retry(
    setup, mcp_postgres, monkeypatch, checkpoint
):
    factory, task_id = setup
    ready(factory, task_id)
    if checkpoint != "no-recovery":
        claim_task(factory, task_id, HEAD, "lead")
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        original_task = task_row(session, task_id)
        original_claim = active_claim_row(session, task_id)
        original_events = event_rows(session, task_id)
    clock = SimpleNamespace(now=1000.0)
    fake_time = SimpleNamespace(time=lambda: clock.now, monotonic=lambda: clock.now)
    session_commits = []
    lookup = PostgresReviewQueueReader.latest_head_handoff_action

    def after_commit(_session):
        session_commits.append(True)
        if checkpoint == "after-session-commit":
            clock.now = 1002.0

    def finish_read(self, candidate, sha):
        action = lookup(self, candidate, sha)
        event.listen(self._session, "after_commit", after_commit)
        if checkpoint != "after-session-commit":
            clock.now = 1002.0
        return action

    with monkeypatch.context() as patch:
        patch.setattr(budget, "time", fake_time)
        patch.setattr(
            PostgresReviewQueueReader, "latest_head_handoff_action", finish_read
        )
        with dispatch_budget(1001.0):
            with pytest.raises(ToolError, match="dispatch deadline exceeded"):
                mcp_postgres("list_review_queue", {})

    assert session_commits == ([True] if checkpoint == "after-session-commit" else [])
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert task_row(session, task_id) == original_task
        assert active_claim_row(session, task_id) == original_claim
        assert event_rows(session, task_id) == original_events
    result = mcp_postgres("list_review_queue", {})["tasks"]
    assert [item["task_id"] for item in result] == [task_id]


def test_active_claim_constraint_rolls_back_head_change(setup):
    factory, task_id = setup
    ready(factory, task_id)
    with pytest.raises(PersistenceUnavailableError) as error:
        with (
            factory.get_scoped_session() as session,
            SqlAlchemyUnitOfWork(session) as uow,
        ):
            session.execute(
                update(Task)
                .where(Task.task_id == task_id)
                .values(current_head_sha=NEXT)
            )
            session.add_all(
                ReviewAttempt(
                    token=token,
                    task_id=task_id,
                    expected_sha=HEAD,
                    reviewer="lead",
                    claimed_at=1.0,
                    expires_at=2.0,
                    status="ACTIVE",
                )
                for token in (f"{task_id}-one", f"{task_id}-two")
            )
            session.flush()
            uow.commit()
    cause = error.value.__context__
    assert isinstance(cause, IntegrityError)
    assert cause.orig.sqlstate == "23505"
    assert cause.orig.diag.constraint_name == "one_active_claim"
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert task_row(session, task_id).current_head_sha == HEAD
        assert active_claim_row(session, task_id) is None


def test_competing_claims_and_ci_fail_closed(setup):
    factory, task_id = setup
    ready(factory, task_id)
    with pytest.raises(WorkflowError, match="CI"):
        claim_task(factory, task_id, HEAD, "lead", require_ci=True)

    start_together = Barrier(2)

    def claim():
        start_together.wait(timeout=5)
        try:
            return claim_task(factory, task_id, HEAD, "lead")
        except WorkflowError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(c is not None for c in claims) == 1
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert active_claim_row(session, task_id) is not None
        assert not PostgresReviewQueueReader(session).has_successful_ci(task_id, NEXT)


def test_claim_ci_query_failure_rolls_back_savepoint_and_fails_closed(
    setup, monkeypatch
):
    factory, task_id = setup
    ready(factory, task_id)
    original = PostgresReviewWrites.has_successful_ci

    def query_with_unavailable_evidence(self, task_id, sha):
        def fail_query(state):
            if state.statement.column_descriptions[0]["entity"] is CITask:
                state.session.connection().execute(text("SELECT 1 / 0"))

        event.listen(self._session, "do_orm_execute", fail_query)
        try:
            assert original(self, task_id, sha) is False
            assert not self._session.in_nested_transaction()
            assert (
                self._session.scalar(
                    select(Task.task_id).where(Task.task_id == task_id)
                )
                == task_id
            )
            return False
        finally:
            event.remove(self._session, "do_orm_execute", fail_query)

    monkeypatch.setattr(
        PostgresReviewWrites, "has_successful_ci", query_with_unavailable_evidence
    )
    with pytest.raises(WorkflowError, match="verified exact-HEAD CI"):
        claim_task(factory, task_id, HEAD, "lead", require_ci=True)
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert active_claim_row(session, task_id) is None
        assert task_row(session, task_id).status == "READY_IN_REVIEW"


def test_ci_repair_requires_matching_failure_evidence(setup, mcp_postgres, monkeypatch):
    factory, task_id = setup
    ready(factory, task_id)
    with factory.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO controller_ci_tasks(task_id,pr_number,head_sha,verdict) VALUES (:id,7,:head,'FAILURE')"
            ),
            {"id": task_id, "head": HEAD},
        )
    with pytest.raises(WorkflowError, match="not authorized"):
        record_handoff(
            factory,
            task_id,
            REPO,
            "task/test",
            BASE,
            NEXT,
            "docs/handoff.md",
            "repair",
        )
    with factory.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO controller_ci_observations(task_id,pr_number,head_sha,verdict,reason,check_run_id,workflow_run_id,run_attempt,observed_at) VALUES (:id,7,:head,'FAILURE','REQUIRED_CHECK_FAILED',1,2,1,'now')"
            ),
            {"id": task_id, "head": HEAD},
        )
    record_handoff(
        factory,
        task_id,
        REPO,
        "task/test",
        BASE,
        NEXT,
        "docs/handoff.md",
        "repair",
    )
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert not PostgresReviewQueueReader(session).has_successful_ci(task_id, NEXT)
        assert (
            PostgresReviewQueueReader(session).latest_head_handoff_action(task_id, NEXT)
            == "CI_REPAIR_HANDOFF"
        )
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with pytest.raises(WorkflowError, match="CI"):
        claim_task(factory, task_id, NEXT, "lead")
    with factory.transaction() as conn:
        conn.execute(
            text("UPDATE controller_ci_tasks SET verdict='SUCCESS' WHERE task_id=:id"),
            {"id": task_id},
        )
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert PostgresReviewQueueReader(session).has_successful_ci(task_id, NEXT)
        assert not PostgresReviewQueueReader(session).has_successful_ci(task_id, HEAD)
    queued = mcp_postgres("list_review_queue", {})["tasks"]
    assert [item["task_id"] for item in queued] == [task_id]
    assert queued[0]["head_sha"] == NEXT
    assert claim_task(factory, task_id, NEXT, "lead")["reviewed_sha"] == NEXT


def test_expired_claim_recovery(setup):
    factory, task_id = setup
    now = [1000.0]
    ready(factory, task_id)
    first = claim_task(factory, task_id, HEAD, "lead", clock=lambda: now[0], lease=10)
    now[0] += 11
    second = claim_task(factory, task_id, HEAD, "lead", clock=lambda: now[0], lease=10)
    with pytest.raises(WorkflowError, match="expired"):
        submit_task(factory, payload(first), "lead", clock=lambda: now[0])
    assert second["claim_token"] != first["claim_token"]
    assert second["review_round"] == 1


def test_reclaim_rolls_back_expiry_on_rejection_and_write_failure(setup, monkeypatch):
    factory, task_id = setup
    ready(factory, task_id)
    first = claim_task(factory, task_id, HEAD, "lead", lease=10)
    old_token = first["claim_token"]
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        original_events = event_rows(session, task_id)

    def expired_clock():
        return 1011.0

    def assert_old_lease_preserved():
        with (
            factory.get_scoped_session() as session,
            SqlAlchemyUnitOfWork(session, read_only=True),
        ):
            assert active_claim_row(session, task_id).token == old_token
            assert task_row(session, task_id).status == "IN_REVIEW"
            assert event_rows(session, task_id) == original_events

    with pytest.raises(WorkflowError, match="stale claim SHA"):
        claim_task(factory, task_id, NEXT, "lead", clock=expired_clock)
    assert_old_lease_preserved()

    original = PostgresReviewWrites.claim

    def fail_after_replacement(self, *args):
        original(self, *args)
        raise RuntimeError("injected claim failure")

    with monkeypatch.context() as patch:
        patch.setattr(PostgresReviewWrites, "claim", fail_after_replacement)
        with pytest.raises(RuntimeError, match="injected claim failure"):
            claim_task(factory, task_id, HEAD, "lead", clock=expired_clock)
    assert_old_lease_preserved()

    new_claim = claim_task(factory, task_id, HEAD, "lead", clock=expired_clock)
    assert new_claim["claim_token"] != old_token
    assert new_claim["review_round"] == 1
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert claim_row(session, old_token, task_id).status == "EXPIRED"
        assert active_claim_row(session, task_id).token == new_claim["claim_token"]
        transitions = event_rows(session, task_id)[len(original_events) :]
        assert [
            (entry.action, entry.previous_status, entry.next_status)
            for entry in transitions
        ] == [
            ("LEASE_EXPIRED", "IN_REVIEW", "READY_IN_REVIEW"),
            ("CLAIM", "READY_IN_REVIEW", "IN_REVIEW"),
        ]


def test_concurrent_identical_authorization_is_idempotent(setup):
    factory, task_id = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: authorize_task(factory, spec(task_id)), range(2)))
    assert len(read_context(factory, task_id)["events"]) == 1


def test_adversarial_text_is_stored_as_data(setup):
    factory, task_id = setup
    ready(factory, task_id)
    attack = "lead' OR 1=1 --"
    claim = claim_task(factory, task_id, HEAD, attack)
    submit_task(factory, payload(claim, summary=attack), attack)
    context = read_context(factory, task_id)
    assert context["previous_reviews"][0]["summary"] == attack
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert PostgresReviewWrites(session).find_claim(task_id, "' OR 1=1 --") is None
        assert len(review_ids(session, task_id)) == 1


def test_row_lock_timeout_rolls_back_and_retry_succeeds(setup, queue_tasks):
    factory, task_id = setup
    ready(factory, task_id)
    rolled_back_id = queue_tasks[0]
    with factory.transaction() as holder:
        holder.execute(
            text("SELECT task_id FROM tasks WHERE task_id=:id FOR UPDATE"),
            {"id": task_id},
        )
        start = time.monotonic()
        with pytest.raises(PersistenceUnavailableError) as error:
            with dispatch_budget(time.time() + 0.6):
                with (
                    factory.get_scoped_session() as session,
                    SqlAlchemyUnitOfWork(session) as uow,
                ):
                    session.add(
                        Task(
                            task_id=rolled_back_id,
                            metadata_text="{}",
                            repository=REPO,
                            branch="branch",
                            base_sha=BASE,
                            requested_effort="medium",
                            require_verified_effort=0,
                            status="AWAITING_HANDOFF",
                            created_at=1.0,
                            updated_at=1.0,
                        )
                    )
                    session.flush()
                    session.scalar(
                        select(Task.task_id)
                        .where(Task.task_id == task_id)
                        .with_for_update()
                    )
                    uow.commit()
        cause = error.value.__context__
        assert isinstance(cause, DBAPIError)
        assert getattr(cause.orig, "sqlstate", None) in {"55P03", "57014"}
        assert time.monotonic() - start < 3.0
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert task_row(session, rolled_back_id) is None
    with factory.get_scoped_session() as session, SqlAlchemyUnitOfWork(session) as uow:
        assert PostgresReviewWrites(session).lock_task(task_id).head == HEAD
        session.execute(
            update(Task).where(Task.task_id == task_id).values(current_head_sha=NEXT)
        )
        uow.commit()
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert task_row(session, task_id).current_head_sha == NEXT


def test_claim_lock_timeout_is_sanitized_and_retry_succeeds(setup):
    factory, task_id = setup
    ready(factory, task_id)
    before = time.monotonic()
    with factory.transaction() as holder:
        holder.execute(
            text("SELECT task_id FROM tasks WHERE task_id=:id FOR UPDATE"),
            {"id": task_id},
        )
        with dispatch_budget(time.time() + 1.5):
            with pytest.raises(
                PersistenceUnavailableError, match="^PostgreSQL unit of work failed$"
            ) as error:
                claim_task(factory, task_id, HEAD, "lead")
    assert error.value.__suppress_context__
    assert time.monotonic() - before < 6
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert active_claim_row(session, task_id) is None
        assert task_row(session, task_id).status == "READY_IN_REVIEW"
        assert [entry.action for entry in event_rows(session, task_id)][-1:] == [
            "HANDOFF"
        ]
    assert claim_task(factory, task_id, HEAD, "lead")["review_round"] == 1


def test_handoff_lock_timeout_is_sanitized_and_retry_succeeds(setup):
    factory, task_id = setup
    authorize_task(factory, spec(task_id))
    before = time.monotonic()
    with factory.transaction() as conn:
        conn.execute(
            text("SELECT task_id FROM tasks WHERE task_id = :id FOR UPDATE"),
            {"id": task_id},
        )
        with dispatch_budget(time.time() + 1.5):
            with pytest.raises(
                PersistenceUnavailableError, match="PostgreSQL unit of work failed"
            ) as error:
                record_handoff(
                    factory,
                    task_id,
                    REPO,
                    "task/test",
                    BASE,
                    HEAD,
                    "docs/handoff.md",
                    "passed",
                )
    assert error.value.__cause__ is None
    assert time.monotonic() - before < 6
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        task = task_row(session, task_id)
        assert task.status == "AWAITING_HANDOFF"
        assert task.current_head_sha is None
        assert len(event_rows(session, task_id)) == 1
    result = record_handoff(
        factory, task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "passed"
    )
    assert result.head_sha == HEAD


def test_handoff_repository_chooses_latest_matching_event_and_ignores_null(setup):
    factory, task_id = setup
    ready(factory, task_id)
    with factory.transaction() as connection:
        for action, sha, path in (
            ("CI_REPAIR_HANDOFF", HEAD, "latest"),
            ("HANDOFF", NEXT, "different head"),
            ("HANDOFF", None, "null head"),
            ("LOCAL_AUTHORIZATION", HEAD, "different action"),
        ):
            connection.execute(
                insert(TaskEvent.__table__).values(
                    task_id=task_id,
                    previous_status=None,
                    next_status="READY_IN_REVIEW",
                    action=action,
                    sha=sha,
                    timestamp=0,
                    metadata=json.dumps({"handoff_file_path": path}),
                )
            )
    with factory.get_scoped_session() as session:
        handoff = PostgresHandoffRepository(session)
        with SqlAlchemyUnitOfWork(session):
            assert handoff.handoff_path(task_id, None) == (False, None)
            assert handoff.handoff_path(task_id, HEAD) == (True, "latest")
            assert handoff.handoff_path(task_id, NEXT) == (True, "different head")


def test_mcp_review_writes_two_rounds_and_duplicate(setup, mcp_postgres, monkeypatch):
    factory, task_id = setup
    ready(factory, task_id)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    claim = mcp_postgres(
        "claim_review", {"task_id": task_id, "expected_head_sha": HEAD}
    )
    first = payload(claim)
    result = mcp_postgres("submit_review", {"submission": first})
    assert result["verdict"] == "CHANGES_REQUESTED" and result["review_round"] == 1
    assert mcp_postgres("submit_review", {"submission": first}) == {
        "task_id": task_id,
        "duplicate": True,
        "reviewed_head_sha": HEAD,
        "verdict": "CHANGES_REQUESTED",
    }
    with pytest.raises(ToolError, match="conflicting duplicate"):
        mcp_postgres("submit_review", {"submission": {**first, "summary": "changed"}})
    record_handoff(
        factory, task_id, REPO, "task/test", BASE, NEXT, "docs/handoff.md", "test"
    )
    claim2 = mcp_postgres(
        "claim_review", {"task_id": task_id, "expected_head_sha": NEXT}
    )
    second = payload(claim2, "APPROVED", prior_resolutions={"F-1": "FIXED"})
    result2 = mcp_postgres("submit_review", {"submission": second})
    assert result2["verdict"] == "APPROVED" and result2["review_round"] == 2
    context = mcp_postgres("get_review_context", {"task_id": task_id})
    result = mcp_postgres("get_review_result", {"task_id": task_id})
    assert context["status"] == result["status"] == "APPROVED"
    assert context["completed_review_rounds"] == 2
    assert [r["round_number"] for r in context["previous_reviews"]] == [1, 2]
    assert context["previous_reviews"][1]["prior_resolutions"] == {"F-1": "FIXED"}
    assert result["latest_reviewed_sha"] == NEXT
    assert result["approval_current"] is None
    assert result["actual_effort"] is None
    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert len(review_ids(session, task_id)) == 2


def test_submit_requires_claim_owner_exact_head_ci_and_verified_effort(setup):
    factory, task_id = setup
    authorize_task(factory, spec(task_id, verified=True))
    record_handoff(
        factory, task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "test"
    )
    claim = claim_task(factory, task_id, HEAD, "lead")
    submission = payload(claim, "APPROVED")
    with factory.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO controller_ci_tasks(task_id,pr_number,head_sha,verdict) "
                "VALUES (:id,7,:head,'SUCCESS')"
            ),
            {"id": task_id, "head": NEXT},
        )

    with pytest.raises(WorkflowError, match="claim owner mismatch"):
        submit_task(factory, submission, "other", require_ci=True)
    with pytest.raises(WorkflowError, match="verified exact-HEAD CI"):
        submit_task(factory, submission, "lead", require_ci=True)
    with factory.transaction() as connection:
        connection.execute(
            text("UPDATE controller_ci_tasks SET head_sha=:head WHERE task_id=:id"),
            {"id": task_id, "head": HEAD},
        )
    with pytest.raises(WorkflowError, match="required effort cannot be verified"):
        submit_task(factory, submission, "lead", require_ci=True)
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert review_ids(session, task_id) == []
        assert active_claim_row(session, task_id).token == claim["claim_token"]
        assert task_row(session, task_id).completed_review_rounds == 0

    failure = payload(claim)
    assert submit_task(factory, failure, "lead", require_ci=True)["review_round"] == 1
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        committed = claim_row(session, claim["claim_token"], task_id)
        events = event_rows(session, task_id)
    assert committed.status == "SUBMITTED"
    with factory.transaction() as connection:
        connection.execute(
            text("UPDATE controller_ci_tasks SET head_sha=:head WHERE task_id=:id"),
            {"id": task_id, "head": NEXT},
        )
    assert submit_task(factory, failure, "lead", require_ci=True)["duplicate"]
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert claim_row(session, claim["claim_token"], task_id) == committed
        assert event_rows(session, task_id) == events


def test_submit_finding_database_error_rolls_back_review_and_allows_retry(setup):
    factory, task_id = setup
    ready(factory, task_id)
    claim = claim_task(factory, task_id, HEAD, "lead")
    valid = payload(claim)
    oversized_line = {**valid, "findings": [{**valid["findings"][0], "line": 2**31}]}
    with pytest.raises(
        PersistenceUnavailableError, match="^PostgreSQL unit of work failed$"
    ):
        submit_task(factory, oversized_line, "lead")
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert review_ids(session, task_id) == []
        assert active_claim_row(session, task_id).token == claim["claim_token"]
        assert task_row(session, task_id).completed_review_rounds == 0
    with factory.transaction() as connection:
        assert (
            connection.scalar(
                select(ReviewFinding.finding_id).where(ReviewFinding.task_id == task_id)
            )
            is None
        )
    assert submit_task(factory, valid, "lead")["review_round"] == 1


def test_mcp_review_write_failure_rolls_back(setup, mcp_postgres, monkeypatch):
    factory, task_id = setup
    ready(factory, task_id)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    claim = mcp_postgres(
        "claim_review", {"task_id": task_id, "expected_head_sha": HEAD}
    )
    original = PostgresReviewWrites.transition

    def fail(self, task, status, action, now, metadata):
        if action == "REVIEW_SUBMITTED":
            raise RuntimeError("injected persistence failure")
        return original(self, task, status, action, now, metadata)

    monkeypatch.setattr(PostgresReviewWrites, "transition", fail)
    with pytest.raises(ToolError, match="injected persistence failure"):
        mcp_postgres("submit_review", {"submission": payload(claim)})
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert review_ids(session, task_id) == []
        assert claim_row(session, claim["claim_token"], task_id).status == "ACTIVE"
        assert task_row(session, task_id).completed_review_rounds == 0
        assert task_row(session, task_id).status == "IN_REVIEW"


@pytest.mark.parametrize("operation", ["claim", "submit"])
@pytest.mark.parametrize(
    "checkpoint", ["before-session-commit", "after-session-commit"]
)
def test_mcp_review_deadline_failure_rolls_back(
    setup, mcp_postgres, monkeypatch, operation, checkpoint
):
    factory, task_id = setup
    ready(factory, task_id)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    claim_arguments = {"task_id": task_id, "expected_head_sha": HEAD}
    if operation == "submit":
        claim = mcp_postgres("claim_review", claim_arguments)
        arguments = {"submission": payload(claim)}
    else:
        arguments = claim_arguments

    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        original_task = task_row(session, task_id)
        original_events = event_rows(session, task_id)
        original_claim = active_claim_row(session, task_id)
    clock = SimpleNamespace(now=1000.0)
    fake_time = SimpleNamespace(time=lambda: clock.now, monotonic=lambda: clock.now)
    original_write = getattr(PostgresReviewWrites, operation)
    session_commits = []

    def after_session_commit(_session):
        session_commits.append(True)
        if checkpoint == "after-session-commit":
            clock.now = 1002.0

    def write_and_expire(self, *args):
        original_write(self, *args)
        event.listen(self._session, "after_commit", after_session_commit)
        if checkpoint == "before-session-commit":
            clock.now = 1002.0

    with monkeypatch.context() as patch:
        patch.setattr(budget, "time", fake_time)
        patch.setattr(PostgresReviewWrites, operation, write_and_expire)
        with dispatch_budget(1001.0):
            with pytest.raises(ToolError, match="dispatch deadline exceeded"):
                mcp_postgres(f"{operation}_review", arguments)

    assert session_commits == ([True] if checkpoint == "after-session-commit" else [])
    with (
        factory.get_scoped_session() as session,
        SqlAlchemyUnitOfWork(session, read_only=True),
    ):
        assert review_ids(session, task_id) == []
        assert task_row(session, task_id) == original_task
        assert event_rows(session, task_id) == original_events
        assert active_claim_row(session, task_id) == original_claim
    result = mcp_postgres(f"{operation}_review", arguments)
    assert result["review_round"] == 1
