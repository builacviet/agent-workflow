"""Layer: Infrastructure
Responsibility: Lock claimed tasks and persist expired leases in the caller's transaction.
Dependencies: Application review lease DTOs, PostgreSQL models and SQLAlchemy Session.
"""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent_workflow.application.review.dtos import (
    ActiveReviewClaim,
    ClaimedReviewTask,
    ReviewLeaseTransition,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.infrastructure.database.models import ReviewAttempt, Task, TaskEvent


class PostgresReviewLeaseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_claimed_tasks(self) -> tuple[ClaimedReviewTask, ...]:
        tasks = self._session.execute(
            select(Task.task_id, Task.current_head_sha)
            .where(Task.status == WorkflowStatus.IN_REVIEW)
            .order_by(Task.task_id)
            .with_for_update()
        )
        return tuple(
            ClaimedReviewTask(task_id, head_sha, WorkflowStatus.IN_REVIEW)
            for task_id, head_sha in tasks
        )

    def find_active_claim(self, task_id: str) -> ActiveReviewClaim | None:
        claim = self._session.execute(
            select(ReviewAttempt.token, ReviewAttempt.expires_at).where(
                ReviewAttempt.task_id == task_id, ReviewAttempt.status == "ACTIVE"
            )
        ).one_or_none()
        return ActiveReviewClaim(claim.token, claim.expires_at) if claim else None

    def expire_claim(self, token: str) -> None:
        self._session.execute(
            update(ReviewAttempt)
            .where(ReviewAttempt.token == token)
            .values(status="EXPIRED")
        )

    def record_transition(self, transition: ReviewLeaseTransition) -> None:
        task = transition.task
        self._session.add(
            TaskEvent(
                task_id=task.task_id,
                previous_status=task.status,
                next_status=transition.next_status,
                action=transition.action,
                sha=task.head_sha,
                timestamp=transition.timestamp,
                metadata_text="{}",
            )
        )
        self._session.flush()
        self._session.execute(
            update(Task)
            .where(Task.task_id == task.task_id)
            .values(status=transition.next_status, updated_at=transition.timestamp)
        )
