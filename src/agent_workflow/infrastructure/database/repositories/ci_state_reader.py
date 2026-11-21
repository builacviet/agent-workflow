"""Read-only PostgreSQL projections for the Controller CI watcher."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.domain.workflow_identity import REPOSITORY, WorkflowError
from agent_workflow.infrastructure.database.session_factory import (
    _set_repository_transaction_options,
)
from agent_workflow.infrastructure.database.unit_of_work import PostgresUnavailableError
from agent_workflow.storage.postgres_models import (
    CIObservation,
    CIProcessedDelivery,
    CITask,
    Task,
    WebhookInboxRow,
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

    def tracked(self, limit: int = 32) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("bounded CI task scan required")
        try:
            with self._session_scope() as session:
                with session.begin() as transaction:
                    _set_repository_transaction_options(
                        session.connection(), read_only=True
                    )
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
                    result = [
                        _TrackedTaskDTO.model_validate(binding).model_dump()
                        for binding in bindings
                    ]
                    if len(result) > limit:
                        raise WorkflowError("too many in-flight CI tasks")
                    transaction.rollback()
                    return result
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        try:
            with self._session_scope() as session:
                with session.begin() as transaction:
                    _set_repository_transaction_options(
                        session.connection(), read_only=True
                    )
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
                    transaction.rollback()
                    return result
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None

    def approved(self, task_id: str, head_sha: str) -> bool:
        try:
            with self._session_scope() as session:
                with session.begin() as transaction:
                    _set_repository_transaction_options(
                        session.connection(), read_only=True
                    )
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
                    result = (
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
                    transaction.rollback()
                    return result
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("bounded webhook scan required")
        try:
            with self._session_scope() as session:
                with session.begin() as transaction:
                    _set_repository_transaction_options(
                        session.connection(), read_only=True
                    )
                    rows = session.scalars(
                        select(WebhookInboxRow)
                        .outerjoin(
                            CIProcessedDelivery,
                            CIProcessedDelivery.delivery_id
                            == WebhookInboxRow.delivery_id,
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
                    transaction.rollback()
                    return result
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None
