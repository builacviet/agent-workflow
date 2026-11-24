"""Layer: Infrastructure
Responsibility: Adapt review queue reads and lease persistence to one PostgreSQL unit.
Dependencies: Application review ports and the PostgreSQL workflow repository.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager

from agent_workflow.application.review.dtos import (
    ActiveReviewClaim,
    ClaimedReviewTask,
    ReviewLeaseTransition,
    ReviewQueueItem,
)
from agent_workflow.application.review.ports import ReviewQueueOperations
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import canonical_repository
from agent_workflow.storage.postgres_workflow import (
    PostgresWorkflowRepository,
    PostgresWorkflowUnit,
)


class PostgresReviewQueueReader:
    def __init__(self, unit: PostgresWorkflowUnit) -> None:
        self._unit = unit

    def read(self, requested_effort: str | None) -> tuple[ReviewQueueItem, ...]:
        return tuple(
            ReviewQueueItem(
                task_id=task["task_id"],
                title=json.loads(task["metadata"])["title"],
                repository=canonical_repository(task["repository"]),
                branch=task["branch"],
                head_sha=task["current_head_sha"],
                requested_effort=task["requested_effort"],
                review_round=task["completed_review_rounds"] + 1,
            )
            for task in self._unit.list_ready_tasks()
            if requested_effort is None or task["requested_effort"] == requested_effort
        )

    def latest_head_handoff_action(
        self, task_id: str, head_sha: str | None
    ) -> str | None:
        handoff = self._unit.find_head_handoff(task_id, head_sha)
        return handoff["action"] if handoff else None

    def has_successful_ci(self, task_id: str, head_sha: str | None) -> bool:
        return self._unit.ci_approved((task_id, head_sha))


class PostgresReviewLeaseStore:
    def __init__(self, unit: PostgresWorkflowUnit) -> None:
        self._unit = unit

    def lock_claimed_tasks(self) -> tuple[ClaimedReviewTask, ...]:
        return tuple(
            ClaimedReviewTask(
                task_id=task["task_id"],
                head_sha=task["current_head_sha"],
                status=WorkflowStatus(task["status"]),
            )
            for task in self._unit.list_claimed_tasks()
        )

    def find_active_claim(self, task_id: str) -> ActiveReviewClaim | None:
        claim = self._unit.find_active_claim((task_id,))
        return ActiveReviewClaim(claim["token"], claim["expires_at"]) if claim else None

    def expire_claim(self, token: str) -> None:
        self._unit.expire_claim((token,))

    def record_transition(self, transition: ReviewLeaseTransition) -> None:
        task = transition.task
        self._unit.insert_event(
            (
                task.task_id,
                task.status,
                transition.next_status,
                transition.action,
                task.head_sha,
                transition.timestamp,
                "{}",
            )
        )
        self._unit.set_status(
            (transition.next_status, transition.timestamp, task.task_id)
        )


class PostgresReviewQueueScope:
    def __init__(self, repository: PostgresWorkflowRepository) -> None:
        self._repository = repository

    @contextmanager
    def __call__(self) -> Iterator[ReviewQueueOperations]:
        with self._repository.transaction() as unit:
            yield PostgresReviewLeaseStore(unit), PostgresReviewQueueReader(unit)
