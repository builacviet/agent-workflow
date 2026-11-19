"""Compare both review-context readers on one migrated, disposable database."""

import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from unittest.mock import Mock
from urllib.parse import quote

import pytest
from sqlalchemy import delete, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from agent_workflow.app_config import AppConfig
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
    GetReviewContextUseCase,
)
from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.domain.service import WorkflowService
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_models import (
    Review,
    ReviewAttempt,
    ReviewFinding,
    Task,
    TaskEvent,
)
from agent_workflow.storage.postgres_workflow import (
    PostgresWorkflowRepository,
    PostgresWorkflowUnit,
)

BASE = "a" * 40
HEAD = "b" * 40
NEXT = "c" * 40
REPO = "builacviet/agent-workflow"
BRANCH = "task/test"
HANDOFF_PATH = "docs/handoff.md"

ContextDatabase = tuple[PostgresFactory, WorkflowService, str]


def task_spec(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": "Synthetic review context",
        "description": "PostgreSQL parity fixture",
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


def finding(finding_id: str) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "severity": "medium",
        "acceptance_criterion": "AC4",
        "file_path": "src/module.py",
        "description": "Missing case",
        "suggested_correction": "Handle absent data",
        "required_verification": "Regression test",
    }


def review_payload(
    claim: dict[str, object],
    verdict: WorkflowStatus,
    *,
    findings: list[dict[str, object]],
    prior_resolutions: dict[str, str],
) -> dict[str, object]:
    return {
        "task_id": claim["task_id"],
        "claim_token": claim["claim_token"],
        "reviewed_head_sha": claim["reviewed_sha"],
        "review_round": claim["review_round"],
        "verdict": verdict.value,
        "summary": "Synthetic review",
        "findings": findings,
        "prior_resolutions": prior_resolutions,
        "requested_effort": "medium",
    }


@pytest.fixture
def context_db() -> Iterator[ContextDatabase]:
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    task_id = f"R2-ARCH-01-{uuid.uuid4().hex}"
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        service = WorkflowService(
            repository=PostgresWorkflowRepository(factory),
            require_ci=False,
            clock=lambda: 1000.0,
        )
        try:
            yield factory, service, task_id
        finally:
            with factory.transaction() as connection:
                for model in (ReviewFinding, Review, ReviewAttempt, TaskEvent, Task):
                    connection.execute(delete(model).where(model.task_id == task_id))


def _assert_parity(
    use_case: GetReviewContextUseCase, service: WorkflowService, task_id: str
) -> None:
    expected = service.get_review_context(task_id)
    actual = use_case.execute(GetReviewContextInput(task_id))
    task = asdict(actual.task)
    for key, value in task.items():
        assert value == (
            tuple(expected[key]) if key == "context_paths" else expected[key]
        )
    assert type(actual.task.require_verified_effort) is type(
        expected["require_verified_effort"]
    )
    assert actual.task.status is WorkflowStatus(expected["status"])
    assert actual.review_round == expected["review_round"]
    assert actual.rounds_remaining == expected["rounds_remaining"]
    assert actual.previous_reviewed_sha == expected["previous_reviewed_sha"]
    assert actual.previous_verdict == expected["previous_verdict"]

    actual_reviews = []
    for review in actual.previous_reviews:
        record = asdict(review)
        record["findings"] = [asdict(finding) for finding in review.findings]
        actual_reviews.append(record)
    assert actual_reviews == expected["previous_reviews"]
    assert [asdict(event) for event in actual.events] == expected["events"]

    # Exercise the inputs a future transport mapper would use for the existing URLs.
    root = f"https://github.com/{actual.task.repository}"
    head = actual.task.current_head_sha
    prior = actual.previous_reviewed_sha
    assert expected["github_references"] == {
        "complete_diff": f"{root}/compare/{actual.task.base_sha}...{head}"
        if head
        else None,
        "correction_diff": f"{root}/compare/{prior}...{head}"
        if prior and head
        else None,
        "context_files": {
            path: f"{root}/blob/{head}/{quote(path, safe='/')}"
            for path in actual.task.context_paths
        }
        if head
        else {},
    }
    assert service.get_review_context(task_id) == expected


def test_context_parity_with_empty_and_two_round_history(
    context_db: ContextDatabase,
) -> None:
    factory, service, task_id = context_db
    use_case = create_get_review_context_use_case(factory)
    service.provision_task(task_spec(task_id))
    _assert_parity(use_case, service, task_id)

    service.register_task_handoff(
        task_id, REPO, BRANCH, BASE, HEAD, HANDOFF_PATH, "first handoff"
    )
    claim = service.claim_review(task_id, HEAD, "lead")
    service.submit_review(
        review_payload(
            claim,
            WorkflowStatus.CHANGES_REQUESTED,
            findings=[finding("F-2"), finding("F-1")],
            prior_resolutions={},
        ),
        "lead",
    )
    _assert_parity(use_case, service, task_id)

    service.register_task_handoff(
        task_id, REPO, BRANCH, BASE, NEXT, HANDOFF_PATH, "second handoff"
    )
    _assert_parity(use_case, service, task_id)
    second = service.claim_review(task_id, NEXT, "lead")
    service.submit_review(
        review_payload(
            second,
            WorkflowStatus.APPROVED,
            findings=[],
            prior_resolutions={"F-1": "FIXED", "F-2": "FIXED"},
        ),
        "lead",
    )
    _assert_parity(use_case, service, task_id)

    missing = f"{task_id}-missing"
    with pytest.raises(WorkflowError, match="^unknown authorized task$"):
        service.get_review_context(missing)
    with pytest.raises(WorkflowError, match="^unknown authorized task$"):
        use_case.execute(GetReviewContextInput(missing))


def test_context_preserves_snapshot_and_closes_borrowed_read_scope(
    context_db: ContextDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, service, task_id = context_db
    service.provision_task(task_spec(task_id))
    request = GetReviewContextInput(task_id)
    original_list = PostgresWorkflowUnit.list_events
    read_connections: list[Connection] = []

    def observe_read(unit: PostgresWorkflowUnit, values: tuple) -> list[dict]:
        connection = unit.session.connection()
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
        service.register_task_handoff(
            task_id, REPO, BRANCH, BASE, HEAD, HANDOFF_PATH, "during read"
        )
        return original_list(unit, values)

    connection_scope = Mock(wraps=factory.connect_session)
    with monkeypatch.context() as patch:
        patch.setattr(factory, "connect_session", connection_scope)
        patch.setattr(PostgresWorkflowUnit, "list_events", observe_read)
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
    factory, service, task_id = context_db
    service.provision_task(task_spec(task_id))
    request = GetReviewContextInput(task_id)
    use_case = create_get_review_context_use_case(factory)
    failed_connections: list[Connection] = []
    rollbacks: list[Connection] = []

    def reject_write(unit: PostgresWorkflowUnit, values: tuple) -> list[dict]:
        connection = unit.session.connection()
        failed_connections.append(connection)
        event.listen(connection, "rollback", rollbacks.append)
        connection.execute(
            text("UPDATE tasks SET current_head_sha = :sha WHERE task_id = :id"),
            {"sha": HEAD, "id": task_id},
        )
        pytest.fail("the read transaction accepted an update")

    with monkeypatch.context() as patch:
        patch.setattr(PostgresWorkflowUnit, "list_reviews", reject_write)
        with pytest.raises(DBAPIError) as error:
            use_case.execute(request)

    assert getattr(error.value.orig, "sqlstate", None) == "25006"
    assert len(failed_connections) == 1
    assert rollbacks == failed_connections
    assert failed_connections[0].closed
    assert use_case.execute(request).task.current_head_sha is None
