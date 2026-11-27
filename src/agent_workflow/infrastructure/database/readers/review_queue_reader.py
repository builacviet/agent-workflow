"""Layer: Infrastructure
Responsibility: Project review queue items and exact-HEAD evidence from the caller's Session.
Dependencies: Application review DTOs, PostgreSQL models and SQLAlchemy Session.
"""

import json

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agent_workflow.application.exceptions import CorruptedPersistenceDataError
from agent_workflow.application.review.dtos import ReviewQueueItem
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.domain.task.handoff import TaskEventAction
from agent_workflow.infrastructure.database.models import CITask, Task, TaskEvent


def _queue_item(task: Task) -> ReviewQueueItem:
    try:
        title = json.loads(task.metadata_text)["title"]
        if not isinstance(title, str):
            raise TypeError
    except (ValueError, TypeError, KeyError, RecursionError):
        raise CorruptedPersistenceDataError(
            "invalid persisted review queue task"
        ) from None
    return ReviewQueueItem(
        task_id=task.task_id,
        title=title,
        repository=task.repository,
        branch=task.branch,
        head_sha=task.current_head_sha,
        requested_effort=task.requested_effort,
        review_round=task.completed_review_rounds + 1,
    )


class PostgresReviewQueueReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def read(self, requested_effort: str | None) -> tuple[ReviewQueueItem, ...]:
        tasks = self._session.scalars(
            select(Task)
            .where(Task.status == WorkflowStatus.READY_IN_REVIEW)
            .order_by(Task.task_id)
        )
        return tuple(
            _queue_item(task)
            for task in tasks
            if requested_effort is None or task.requested_effort == requested_effort
        )

    def latest_head_handoff_action(
        self, task_id: str, head_sha: str | None
    ) -> str | None:
        if head_sha is None:
            return None
        return self._session.scalar(
            select(TaskEvent.action)
            .where(
                TaskEvent.task_id == task_id,
                TaskEvent.sha == head_sha,
                TaskEvent.action.in_(tuple(TaskEventAction)),
            )
            .order_by(TaskEvent.event_id.desc())
            .limit(1)
        )

    def has_successful_ci(self, task_id: str, head_sha: str | None) -> bool:
        if head_sha is None:
            return False
        statement = select(CITask.task_id).where(
            CITask.task_id == task_id,
            CITask.head_sha == head_sha,
            CITask.verdict == CiVerdict.SUCCESS,
        )
        try:
            with self._session.begin_nested():
                return self._session.scalar(statement) is not None
        except DBAPIError:
            return False
