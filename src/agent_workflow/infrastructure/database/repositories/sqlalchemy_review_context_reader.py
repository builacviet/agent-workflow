"""Read review snapshots through the existing PostgreSQL Workflow unit."""

import json

from pydantic import ConfigDict, TypeAdapter

from agent_workflow.application.review.dtos import (
    PreviousReview,
    ReviewContextSnapshot,
    ReviewEvent,
    ReviewFinding,
    ReviewTask,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import canonical_repository
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_workflow import (
    PostgresWorkflowRepository,
    PostgresWorkflowUnit,
)

_CONTEXT_PATHS = TypeAdapter(
    tuple[str, ...], config=ConfigDict(hide_input_in_errors=True)
)
_PRIOR_RESOLUTIONS = TypeAdapter(
    dict[str, str], config=ConfigDict(hide_input_in_errors=True)
)


class SqlAlchemyReviewContextReader:
    def __init__(self, factory: PostgresFactory) -> None:
        self._repository = PostgresWorkflowRepository(factory)

    def read(self, task_id: str) -> ReviewContextSnapshot | None:
        with self._repository.read() as db:
            record = db.find_task((task_id,))
            if record is None:
                return None

            task = ReviewTask(
                task_id=record["task_id"],
                repository=canonical_repository(record["repository"]),
                branch=record["branch"],
                base_sha=record["base_sha"],
                current_head_sha=record["current_head_sha"],
                requested_effort=record["requested_effort"],
                require_verified_effort=record["require_verified_effort"],
                max_review_rounds=record["max_review_rounds"],
                completed_review_rounds=record["completed_review_rounds"],
                status=WorkflowStatus(record["status"]),
                created_at=record["created_at"],
                updated_at=record["updated_at"],
                context_paths=_CONTEXT_PATHS.validate_python(
                    json.loads(record["metadata"])["context_paths"]
                ),
            )
            return ReviewContextSnapshot(
                task=task,
                previous_reviews=_read_reviews(db, task_id),
                events=_read_events(db, task_id),
            )


def _read_reviews(db: PostgresWorkflowUnit, task_id: str) -> tuple[PreviousReview, ...]:
    reviews = []
    for row in db.list_reviews((task_id,)):
        findings = tuple(
            ReviewFinding(**finding)
            for finding in db.list_findings((row["review_id"],))
        )
        row["verdict"] = WorkflowStatus(row["verdict"])
        row["prior_resolutions"] = _PRIOR_RESOLUTIONS.validate_json(
            row["prior_resolutions"]
        )
        reviews.append(PreviousReview(**row, findings=findings))
    return tuple(reviews)


def _read_events(db: PostgresWorkflowUnit, task_id: str) -> tuple[ReviewEvent, ...]:
    events = []
    for row in db.list_events((task_id,)):
        if row["previous_status"] is not None:
            row["previous_status"] = WorkflowStatus(row["previous_status"])
        row["next_status"] = WorkflowStatus(row["next_status"])
        events.append(ReviewEvent(**row))
    return tuple(events)
