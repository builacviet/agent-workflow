"""Layer: Infrastructure
Responsibility: Project persisted task fields for review context.
Dependencies: Application review DTOs, SQLAlchemy models and session controls.
"""

import json

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_workflow.application.review.dtos import ReviewTask
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import canonical_repository
from agent_workflow.infrastructure.database.models import Task
from agent_workflow.infrastructure.database.unit_of_work import (
    set_workflow_query_timeout,
)

_CONTEXT_PATHS = TypeAdapter(tuple[str, ...])


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_task(self, task_id: str) -> ReviewTask | None:
        set_workflow_query_timeout(self._session)
        row = self._session.execute(
            select(Task).where(Task.task_id == task_id)
        ).scalar_one_or_none()
        if row is None:
            return None

        try:
            context_paths = _CONTEXT_PATHS.validate_python(
                json.loads(row.metadata_text)["context_paths"]
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("Invalid persisted task context paths") from None

        return ReviewTask(
            task_id=row.task_id,
            repository=canonical_repository(row.repository),
            branch=row.branch,
            base_sha=row.base_sha,
            current_head_sha=row.current_head_sha,
            requested_effort=row.requested_effort,
            require_verified_effort=row.require_verified_effort,
            max_review_rounds=row.max_review_rounds,
            completed_review_rounds=row.completed_review_rounds,
            status=WorkflowStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            context_paths=context_paths,
            spec_json=row.metadata_text,
        )
