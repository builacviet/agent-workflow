"""Layer: Infrastructure
Responsibility: Persist handoff transitions in the caller's PostgreSQL transaction.
Dependencies: Task handoff contracts, canonical mappings and SQLAlchemy Session.
"""

import json

from sqlalchemy import exists, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agent_workflow.application.exceptions import CorruptedPersistenceDataError
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.domain.task.handoff import (
    HandoffRequest,
    HandoffTask,
    TaskEventAction,
)
from agent_workflow.infrastructure.database.models import (
    CIObservation,
    CITask,
    Task,
    TaskEvent,
)


class PostgresHandoffRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_task(self, task_id: str) -> HandoffTask | None:
        row = self._session.scalar(
            select(Task).where(Task.task_id == task_id).with_for_update()
        )
        if row is None:
            return None
        try:
            status = WorkflowStatus(row.status)
            paths = json.loads(row.metadata_text)["context_paths"]
            if not isinstance(paths, list) or not all(
                isinstance(path, str) for path in paths
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError):
            raise CorruptedPersistenceDataError(
                "invalid persisted handoff task"
            ) from None
        return HandoffTask(
            row.task_id,
            row.repository,
            row.branch,
            row.base_sha,
            row.current_head_sha,
            status,
            row.completed_review_rounds,
            tuple(paths),
        )

    def handoff_path(self, task_id: str, sha: str | None) -> tuple[bool, str | None]:
        if sha is None:
            return False, None
        metadata = self._session.scalar(
            select(TaskEvent.metadata_text)
            .where(
                TaskEvent.task_id == task_id,
                TaskEvent.sha == sha,
                TaskEvent.action.in_(tuple(TaskEventAction)),
            )
            .order_by(TaskEvent.event_id.desc())
            .limit(1)
        )
        if metadata is None:
            return False, None
        try:
            path = json.loads(metadata).get("handoff_file_path")
            if path is not None and not isinstance(path, str):
                raise ValueError
        except (ValueError, AttributeError, RecursionError):
            raise CorruptedPersistenceDataError(
                "invalid persisted handoff event"
            ) from None
        return True, path

    def advance_failed_ci_head(self, task_id: str, old_head: str, new_head: str) -> int:
        if new_head == old_head:
            raise BusinessRuleError("CI repair requires a different HEAD")
        failure = exists(
            select(CIObservation.id).where(
                CIObservation.task_id == CITask.task_id,
                CIObservation.pr_number == CITask.pr_number,
                CIObservation.head_sha == CITask.head_sha,
                CIObservation.verdict == CITask.verdict,
                CIObservation.reason == "REQUIRED_CHECK_FAILED",
                CIObservation.check_run_id > 0,
                CIObservation.workflow_run_id > 0,
                CIObservation.run_attempt > 0,
            )
        )
        seen = exists(
            select(CIObservation.id).where(
                CIObservation.task_id == CITask.task_id,
                CIObservation.head_sha == new_head,
            )
        )
        stmt = (
            select(CITask.pr_number)
            .where(
                CITask.task_id == task_id,
                CITask.head_sha == old_head,
                CITask.verdict == CiVerdict.FAILURE,
                CITask.pr_number > 0,
                CITask.repair_heads < 2,
                failure,
                ~seen,
            )
            .with_for_update()
        )
        try:
            with self._session.begin_nested():
                pr_number = self._session.scalar(stmt)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) not in {"42P01", "42703"}:
                raise
            raise BusinessRuleError("CI repair evidence is unavailable") from None
        if pr_number is None:
            raise BusinessRuleError("CI repair HEAD not authorized")
        changed = self._session.execute(
            update(CITask)
            .where(CITask.task_id == task_id)
            .values(
                head_sha=new_head,
                verdict=CiVerdict.BLOCKED,
                repair_heads=CITask.repair_heads + 1,
            )
            .returning(CITask.task_id)
        ).scalar_one_or_none()
        if changed is None:
            raise BusinessRuleError("CI repair update failed")
        return pr_number

    def record(
        self,
        task: HandoffTask,
        request: HandoffRequest,
        action: TaskEventAction,
        metadata: dict[str, str | int],
        now: float,
    ) -> None:
        self._session.execute(
            update(Task)
            .where(Task.task_id == task.task_id)
            .values(
                current_head_sha=request.new_head_sha,
                status=WorkflowStatus.READY_IN_REVIEW,
                updated_at=now,
            )
        )
        self._session.add(
            TaskEvent(
                task_id=task.task_id,
                previous_status=task.status,
                next_status=WorkflowStatus.READY_IN_REVIEW,
                action=action,
                sha=request.new_head_sha,
                timestamp=now,
                metadata_text=json.dumps(
                    metadata, sort_keys=True, separators=(",", ":"), allow_nan=False
                ),
            )
        )
        self._session.flush()
