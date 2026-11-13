"""Opt-in PostgreSQL Controller CI evidence and inbox settlement."""

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from agent_workflow.domain.ci_evidence import (
    CiEvidence,
    WebhookNotification,
    validate_delivery_id,
    validate_evidence,
)
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.domain.workflow_identity import (
    REPOSITORY,
    WorkflowError,
    validate_sha,
)
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_models import (
    CIObservation,
    CIProcessedDelivery,
    CITask,
    Task,
    WebhookInboxRow,
)

_OPERATION_ERROR = "PostgreSQL Controller CI operation failed"


class _TrackedTaskDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    pr_number: int
    head_sha: str
    verdict: str
    repair_heads: int


class _NotificationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_id: str
    event: str
    action: str
    repository: str
    installation_id: int
    subject_id: int
    head_sha: str


class PostgresControllerCiState:
    """Use a caller-owned PostgreSQL factory for trusted Controller state."""

    def __init__(self, factory: PostgresFactory) -> None:
        self._factory = factory

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None:
        """Bind the initial PR or replay the exact persisted binding."""
        validate_sha(head_sha)
        if (
            not isinstance(task_id, str)
            or not task_id
            or type(pr_number) is not int
            or not 1 <= pr_number < 2**31
        ):
            raise WorkflowError("invalid CI tracking identity")
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            # Workflow handoffs also lock tasks before touching the CI binding.
            task = session.get(Task, task_id, with_for_update=True)
            if (
                task is None
                or task.status != WorkflowStatus.READY_IN_REVIEW
                or task.current_head_sha != head_sha
                or task.repository != REPOSITORY
            ):
                raise WorkflowError("CI tracking requires authorized exact task HEAD")
            binding = session.get(CITask, task_id, with_for_update=True)
            if binding is None:
                session.add(
                    CITask(
                        task_id=task_id,
                        pr_number=pr_number,
                        head_sha=head_sha,
                        verdict=CiVerdict.BLOCKED,
                    )
                )
                return
            if binding.pr_number != pr_number:
                raise WorkflowError("CI tracking PR differs from persisted binding")
            if binding.head_sha != head_sha:
                raise WorkflowError("CI tracking HEAD differs from persisted binding")

    def tracked(self, limit: int = 32) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("bounded CI task scan required")
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            bindings = session.scalars(
                select(CITask)
                .join(Task, Task.task_id == CITask.task_id)
                .where(
                    Task.status == WorkflowStatus.READY_IN_REVIEW,
                    Task.current_head_sha == CITask.head_sha,
                    Task.repository == REPOSITORY,
                )
                .order_by(CITask.task_id)
                .limit(limit + 1)
            ).all()
            if len(bindings) > limit:
                raise WorkflowError("too many in-flight CI tasks")
            return [
                _TrackedTaskDTO.model_validate(binding).model_dump()
                for binding in bindings
            ]

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            return self._has_failure(session, task_id, head_sha)

    @staticmethod
    def _has_failure(session: Session, task_id: str, head_sha: str) -> bool:
        failure = (
            select(CIObservation.id)
            .where(
                CIObservation.task_id == task_id,
                CIObservation.head_sha == head_sha,
                CIObservation.verdict == CiVerdict.FAILURE,
            )
            .exists()
        )
        return bool(session.scalar(select(failure)))

    def record(self, evidence: CiEvidence) -> None:
        """Append the observation and update the exact binding atomically."""
        validate_evidence(evidence)
        validate_sha(evidence.expected_head_sha)
        if type(evidence.pr_number) is not int or evidence.pr_number < 1:
            raise WorkflowError("invalid CI evidence PR")
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            task = session.get(Task, evidence.task_id, with_for_update=True)
            binding = session.get(CITask, evidence.task_id, with_for_update=True)
            if (
                task is None
                or task.repository != REPOSITORY
                or evidence.repository != task.repository
                or task.current_head_sha != evidence.expected_head_sha
                or binding is None
                or binding.pr_number != evidence.pr_number
                or binding.head_sha != evidence.expected_head_sha
            ):
                raise WorkflowError("CI evidence mismatched task/PR/HEAD")
            had_failure = self._has_failure(
                session, evidence.task_id, evidence.expected_head_sha
            )
            session.add(
                CIObservation(
                    task_id=evidence.task_id,
                    pr_number=evidence.pr_number,
                    head_sha=evidence.expected_head_sha,
                    verdict=evidence.verdict,
                    reason=evidence.reason,
                    check_run_id=evidence.check_run_id,
                    observed_at=evidence.observed_at,
                    workflow_run_id=evidence.workflow_run_id,
                    run_attempt=evidence.run_attempt,
                )
            )
            session.flush()
            binding.verdict = CiVerdict.FAILURE if had_failure else evidence.verdict

    def approved(self, task_id: str, head_sha: str) -> bool:
        try:
            validate_sha(head_sha)
        except WorkflowError:
            return False
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            success = (
                select(CIObservation.id)
                .where(
                    CIObservation.task_id == CITask.task_id,
                    CIObservation.pr_number == CITask.pr_number,
                    CIObservation.head_sha == CITask.head_sha,
                    CIObservation.verdict == CiVerdict.SUCCESS,
                    CIObservation.reason == "REQUIRED_CHECK_PASSED",
                    CIObservation.check_run_id > 0,
                    CIObservation.workflow_run_id > 0,
                    CIObservation.run_attempt > 0,
                )
                .exists()
            )
            return (
                session.scalar(
                    select(CITask.task_id)
                    .join(Task, Task.task_id == CITask.task_id)
                    .where(
                        CITask.task_id == task_id,
                        CITask.head_sha == head_sha,
                        CITask.verdict == CiVerdict.SUCCESS,
                        Task.current_head_sha == CITask.head_sha,
                        Task.repository == REPOSITORY,
                        success,
                    )
                )
                is not None
            )

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("bounded webhook scan required")
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            inbox, processed = WebhookInboxRow, CIProcessedDelivery
            rows = session.scalars(
                select(inbox)
                .outerjoin(processed, processed.delivery_id == inbox.delivery_id)
                .where(processed.delivery_id.is_(None))
                .order_by(inbox.delivery_id)
                .limit(limit)
            ).all()
            return [
                WebhookNotification(**_NotificationDTO.model_validate(row).model_dump())
                for row in rows
            ]

    def mark_processed(self, delivery_id: str) -> None:
        validate_delivery_id(delivery_id)
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            inbox_id = session.scalar(
                select(WebhookInboxRow.delivery_id)
                .where(WebhookInboxRow.delivery_id == delivery_id)
                .with_for_update(read=True, key_share=True)
            )
            if inbox_id is None:
                raise WorkflowError("unknown webhook delivery")
            processed = CIProcessedDelivery.__table__
            session.execute(
                insert(processed)
                .values(delivery_id=delivery_id)
                .on_conflict_do_nothing(index_elements=[processed.c.delivery_id])
            )
