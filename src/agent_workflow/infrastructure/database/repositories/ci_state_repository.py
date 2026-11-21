"""Atomic PostgreSQL writes for the Controller CI watcher."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent_workflow.domain.ci_evidence import (
    CiEvidence,
    validate_delivery_id,
    validate_evidence,
)
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.domain.workflow_identity import (
    REPOSITORY,
    WorkflowError,
    validate_sha,
)
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


class SqlAlchemyCiStateRepository:
    """Own one fresh Session transaction for each durable CI state write."""

    def __init__(self, session_scope: SessionScope) -> None:
        self._session_scope = session_scope

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
        try:
            with self._session_scope() as session:
                with session.begin():
                    _set_repository_transaction_options(
                        session.connection(), read_only=False
                    )
                    task = session.get(Task, task_id, with_for_update=True)
                    if (
                        task is None
                        or task.status != WorkflowStatus.READY_IN_REVIEW
                        or task.current_head_sha != head_sha
                        or task.repository != REPOSITORY
                    ):
                        raise WorkflowError(
                            "CI tracking requires authorized exact task HEAD"
                        )
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
                        raise WorkflowError(
                            "CI tracking PR differs from persisted binding"
                        )
                    if binding.head_sha != head_sha:
                        raise WorkflowError(
                            "CI tracking HEAD differs from persisted binding"
                        )
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None

    def record(self, evidence: CiEvidence) -> None:
        """Append and apply evidence in the same atomic transaction."""
        validate_evidence(evidence)
        validate_sha(evidence.expected_head_sha)
        if type(evidence.pr_number) is not int or evidence.pr_number < 1:
            raise WorkflowError("invalid CI evidence PR")
        try:
            with self._session_scope() as session:
                with session.begin():
                    _set_repository_transaction_options(
                        session.connection(), read_only=False
                    )
                    task = session.get(Task, evidence.task_id, with_for_update=True)
                    binding = session.get(
                        CITask, evidence.task_id, with_for_update=True
                    )
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
                    binding.verdict = (
                        CiVerdict.FAILURE if had_failure else evidence.verdict
                    )
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None

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

    def mark_processed(self, delivery_id: str) -> None:
        validate_delivery_id(delivery_id)
        try:
            with self._session_scope() as session:
                with session.begin():
                    _set_repository_transaction_options(
                        session.connection(), read_only=False
                    )
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
                        .on_conflict_do_nothing(
                            index_elements=[processed.c.delivery_id]
                        )
                    )
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None
