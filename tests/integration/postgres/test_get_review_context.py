"""Verify current review-context reader on disposable PostgreSQL."""

import os
import time
import uuid
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, event, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from agent_workflow.app_config import AppConfig
from agent_workflow.application.review.dtos import PreviousReview, ReviewEvent
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
)
from agent_workflow.application.task.use_cases.register_task_handoff import (
    RegisterTaskHandoffUseCase,
)
from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.composition.task_deps import create_authorize_task_use_case
from agent_workflow.domain.task.handoff import HandoffRequest
from agent_workflow.infrastructure.database.repositories.review_repository import (
    SqlAlchemyReviewRepository,
)
from agent_workflow.infrastructure.database.repositories.task_handoff import (
    PostgresHandoffScope,
)
from agent_workflow.infrastructure.database.session_factory import (
    PostgresUnavailableError,
)
from agent_workflow.interfaces.cli.task_spec import ProvisionTaskSpec
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_models import (
    Review,
    ReviewAttempt,
    ReviewFinding,
    Task,
    TaskEvent,
)
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository

BASE = "a" * 40
HEAD = "b" * 40
REPO = "builacviet/agent-workflow"
BRANCH = "task/test"
HANDOFF_PATH = "docs/handoff.md"

ContextDatabase = tuple[PostgresFactory, str]


def task_spec(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": "Synthetic review context",
        "description": "PostgreSQL context fixture",
        "acceptance_criteria": ["AC4"],
        "definition_of_done": "Tests pass",
        "repository": REPO,
        "branch": BRANCH,
        "base_sha": BASE,
        "requested_effort": "medium",
        "risk_level": "low",
        "require_verified_effort": False,
        "context_paths": [HANDOFF_PATH, "docs/review context.md"],
        "synthetic": True,
    }


@pytest.fixture
def context_db() -> Iterator[ContextDatabase]:
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    task_id = f"R2-ARCH-01-{uuid.uuid4().hex}"
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        try:
            yield factory, task_id
        finally:
            with factory.transaction() as connection:
                for model in (ReviewFinding, Review, ReviewAttempt, TaskEvent, Task):
                    connection.execute(delete(model).where(model.task_id == task_id))


def test_context_preserves_snapshot_and_closes_borrowed_read_scope(
    context_db: ContextDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, task_id = context_db
    with create_authorize_task_use_case(factory) as operation:
        operation.execute(ProvisionTaskSpec.model_validate(task_spec(task_id)).to_command())
    request = GetReviewContextInput(task_id)
    original_list = SqlAlchemyReviewRepository.list_events
    read_connections: list[Connection] = []

    def observe_read(
        repository: SqlAlchemyReviewRepository, requested_task_id: str
    ) -> tuple[ReviewEvent, ...]:
        connection = repository._session.connection()
        read_connections.append(connection)
        assert (
            connection.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        )
        assert connection.scalar(text("SHOW transaction_read_only")) == "on"
        timeout = connection.scalar(
            text(
                "SELECT setting::int FROM pg_settings WHERE name = 'statement_timeout'"
            )
        )
        assert 0 < timeout <= 15_000
        RegisterTaskHandoffUseCase(
            PostgresHandoffScope(PostgresWorkflowRepository(factory)),
            clock=lambda: 1000.0,
        ).execute(HandoffRequest(task_id, REPO, BRANCH, BASE, HEAD, HANDOFF_PATH, "during read"))
        return original_list(repository, requested_task_id)

    connection_scope = Mock(wraps=factory.connect_session)
    with monkeypatch.context() as patch:
        patch.setattr(factory, "connect_session", connection_scope)
        patch.setattr(SqlAlchemyReviewRepository, "list_events", observe_read)
        use_case = create_get_review_context_use_case(factory)
        connection_scope.assert_not_called()
        with dispatch_budget(time.time() + 15):
            result = use_case.execute(request)
    # One connection for the context, plus the deliberately concurrent handoff.
    assert connection_scope.call_count == 2
    assert len(read_connections) == 1
    assert read_connections[0].closed
    assert result.task.current_head_sha is None
    assert [event.action for event in result.events] == ["LOCAL_AUTHORIZATION"]
    assert use_case.execute(request).task.current_head_sha == HEAD


def test_context_read_failure_rolls_back_and_factory_can_be_reused(
    context_db: ContextDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, task_id = context_db
    with create_authorize_task_use_case(factory) as operation:
        operation.execute(ProvisionTaskSpec.model_validate(task_spec(task_id)).to_command())
    request = GetReviewContextInput(task_id)
    use_case = create_get_review_context_use_case(factory)
    failed_connections: list[Connection] = []
    rollbacks: list[Connection] = []

    def reject_write(
        repository: SqlAlchemyReviewRepository, _requested_task_id: str
    ) -> tuple[PreviousReview, ...]:
        connection = repository._session.connection()
        failed_connections.append(connection)
        event.listen(connection, "rollback", rollbacks.append)
        connection.execute(
            text("UPDATE tasks SET current_head_sha = :sha WHERE task_id = :id"),
            {"sha": HEAD, "id": task_id},
        )
        pytest.fail("the read transaction accepted an update")

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyReviewRepository, "list_reviews", reject_write)
        with pytest.raises(
            PostgresUnavailableError, match="^PostgreSQL unit of work failed$"
        ) as error:
            use_case.execute(request)

    assert error.value.__suppress_context__
    cause = error.value.__context__
    assert isinstance(cause, DBAPIError)
    assert getattr(cause.orig, "sqlstate", None) == "25006"
    assert len(failed_connections) == 1
    assert rollbacks == failed_connections
    assert failed_connections[0].closed
    assert use_case.execute(request).task.current_head_sha is None


def test_context_rejects_unknown_persisted_event_status(
    context_db: ContextDatabase,
) -> None:
    factory, task_id = context_db
    with create_authorize_task_use_case(factory) as operation:
        operation.execute(ProvisionTaskSpec.model_validate(task_spec(task_id)).to_command())
    with factory.transaction() as connection:
        connection.execute(
            update(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .values(next_status="UNKNOWN_WORKFLOW_STATUS")
        )

    use_case = create_get_review_context_use_case(factory)
    with pytest.raises(ValueError, match="is not a valid WorkflowStatus"):
        use_case.execute(GetReviewContextInput(task_id))


def test_context_rejects_invalid_metadata_without_exposing_values(
    context_db: ContextDatabase,
) -> None:
    factory, task_id = context_db
    with create_authorize_task_use_case(factory) as operation:
        operation.execute(ProvisionTaskSpec.model_validate(task_spec(task_id)).to_command())
    with factory.transaction() as connection:
        connection.execute(
            update(Task)
            .where(Task.task_id == task_id)
            .values(metadata_text='{"context_paths":{"private":"value"}}')
        )

    use_case = create_get_review_context_use_case(factory)
    with pytest.raises(
        ValueError, match="^Invalid persisted task context paths$"
    ) as exc:
        use_case.execute(GetReviewContextInput(task_id))
    assert type(exc.value) is ValueError
    assert exc.value.__suppress_context__
