"""Layer: Infrastructure
Responsibility: Persist handoff transitions within a locked PostgreSQL scope.
Dependencies: Task handoff contracts and PostgreSQL workflow unit.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager

from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.task.handoff import (
    HandoffRequest,
    HandoffTask,
    TaskEventAction,
)
from agent_workflow.infrastructure.database.repositories.review_writes import encode
from agent_workflow.storage.postgres_workflow import (
    PostgresWorkflowRepository,
    PostgresWorkflowUnit,
)


class PostgresHandoffRepository:
    def __init__(self, unit: PostgresWorkflowUnit):
        self.unit = unit

    def lock_task(self, task_id: str) -> HandoffTask | None:
        row = self.unit.find_task((task_id,))
        if row is None:
            return None
        return HandoffTask(
            row["task_id"],
            row["repository"],
            row["branch"],
            row["base_sha"],
            row["current_head_sha"],
            WorkflowStatus(row["status"]),
            row["completed_review_rounds"],
            tuple(json.loads(row["metadata"])["context_paths"]),
        )

    def handoff_path(self, task_id: str, sha: str | None) -> tuple[bool, str | None]:
        event = self.unit.find_head_handoff(task_id, sha)
        return (
            (False, None)
            if event is None
            else (True, json.loads(event["metadata"]).get("handoff_file_path"))
        )

    def advance_failed_ci_head(self, task_id: str, old: str, new: str) -> int:
        return self.unit.advance_failed_ci_head(task_id, old, new)

    def record(
        self,
        task: HandoffTask,
        request: HandoffRequest,
        action: TaskEventAction,
        metadata: dict,
        now: float,
    ) -> None:
        self.unit.set_head((request.new_head_sha, task.task_id))
        self.unit.insert_event(
            (
                task.task_id,
                task.status,
                WorkflowStatus.READY_IN_REVIEW,
                action,
                request.new_head_sha,
                now,
                encode(metadata),
            )
        )
        self.unit.set_status((WorkflowStatus.READY_IN_REVIEW, now, task.task_id))


class PostgresHandoffScope:
    def __init__(self, repository: PostgresWorkflowRepository):
        self.repository = repository

    @contextmanager
    def __call__(self) -> Iterator[PostgresHandoffRepository]:
        with self.repository.transaction() as unit:
            yield PostgresHandoffRepository(unit)
