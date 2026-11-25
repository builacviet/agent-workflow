"""Read-only PostgreSQL projections for the Controller CI watcher."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent_workflow.application.ci.dtos import CiTrackedTaskSnapshot
from agent_workflow.application.exceptions import (
    CorruptedPersistenceDataError,
    PersistenceUnavailableError,
)
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.ci_tracking import CiApprovalSnapshot
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.domain.workflow_identity import REPOSITORY, WorkflowError
from agent_workflow.infrastructure.database.models import (
    CIObservation,
    CIProcessedDelivery,
    CITask,
    Task,
    WebhookInboxRow,
)
from agent_workflow.infrastructure.database.unit_of_work import (
    _set_repository_transaction_options,
)

_OPERATION_ERROR = "PostgreSQL Controller CI operation failed"
SessionScope = Callable[[], AbstractContextManager[Session]]


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


class SqlAlchemyCiStateReader:
    """Materialize bounded CI projections in READ ONLY transactions."""

    def __init__(self, session_scope: SessionScope) -> None:
        self._session_scope = session_scope

    @contextmanager
    def _read_session(self) -> Iterator[Session]:
        """Yield one READ ONLY Session and end its transaction without COMMIT."""
        with self._session_scope() as session:
            with session.begin() as transaction:
                try:
                    _set_repository_transaction_options(
                        session.connection(), read_only=True
                    )
                    yield session
                finally:
                    if transaction.is_active:
                        transaction.rollback()

    def tracked(self, limit: int = 32) -> tuple[CiTrackedTaskSnapshot, ...]:
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("bounded CI task scan required")
        try:
            with self._read_session() as session:
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
                validated = (
                    _TrackedTaskDTO.model_validate(binding) for binding in bindings
                )
                result = tuple(
                    CiTrackedTaskSnapshot(
                        task_id=task.task_id,
                        pr_number=task.pr_number,
                        head_sha=task.head_sha,
                        verdict=task.verdict,
                        repair_heads=task.repair_heads,
                    )
                    for task in validated
                )
                if len(result) > limit:
                    raise WorkflowError("too many in-flight CI tasks")
                return result
        except ValidationError:
            raise CorruptedPersistenceDataError(_OPERATION_ERROR) from None
        except SQLAlchemyError:
            raise PersistenceUnavailableError(_OPERATION_ERROR) from None

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        try:
            with self._read_session() as session:
                failure = (
                    select(CIObservation.id)
                    .where(
                        CIObservation.task_id == task_id,
                        CIObservation.head_sha == head_sha,
                        CIObservation.verdict == CiVerdict.FAILURE,
                    )
                    .exists()
                )
                result = bool(session.scalar(select(failure)))
                return result
        except ValidationError:
            raise CorruptedPersistenceDataError(_OPERATION_ERROR) from None
        except SQLAlchemyError:
            raise PersistenceUnavailableError(_OPERATION_ERROR) from None

    def approved(self, task_id: str, head_sha: str) -> CiApprovalSnapshot | None:
        """Return a materialized approval projection; Application owns verdict."""
        try:
            with self._read_session() as session:
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
                row = session.execute(
                    select(
                        CITask.head_sha,
                        CITask.verdict,
                        Task.current_head_sha,
                        Task.repository,
                        success,
                    )
                    .join(Task, Task.task_id == CITask.task_id)
                    .where(CITask.task_id == task_id)
                ).one_or_none()
                if row is None:
                    return None
                return CiApprovalSnapshot(
                    binding_head_sha=row[0],
                    verdict=row[1],
                    task_head_sha=row[2],
                    repository=row[3],
                    verified_success=bool(row[4]),
                )
        except ValidationError:
            raise CorruptedPersistenceDataError(_OPERATION_ERROR) from None
        except SQLAlchemyError:
            raise PersistenceUnavailableError(_OPERATION_ERROR) from None

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("bounded webhook scan required")
        try:
            with self._read_session() as session:
                rows = session.scalars(
                    select(WebhookInboxRow)
                    .outerjoin(
                        CIProcessedDelivery,
                        CIProcessedDelivery.delivery_id == WebhookInboxRow.delivery_id,
                    )
                    .where(CIProcessedDelivery.delivery_id.is_(None))
                    .order_by(WebhookInboxRow.delivery_id)
                    .limit(limit)
                ).all()
                result = [
                    WebhookNotification(
                        **_NotificationDTO.model_validate(row).model_dump()
                    )
                    for row in rows
                ]
                return result
        except ValidationError:
            raise CorruptedPersistenceDataError(_OPERATION_ERROR) from None
        except SQLAlchemyError:
            raise PersistenceUnavailableError(_OPERATION_ERROR) from None
