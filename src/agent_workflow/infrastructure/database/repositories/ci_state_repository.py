"""Atomic PostgreSQL writes for the Controller CI watcher."""

from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent_workflow.application.exceptions import PersistenceUnavailableError
from agent_workflow.domain.ci_evidence import CiEvidence
from agent_workflow.domain.ci_tracking import CiRecordSnapshot, CiTrackingSnapshot
from agent_workflow.domain.states import CiVerdict
from agent_workflow.infrastructure.database.session_factory import (
    _set_repository_transaction_options,
)
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
    """Atomic CI writes; a transaction yields a fresh session-bound adapter."""

    def __init__(
        self, session_scope: SessionScope, *, _session: Session | None = None
    ) -> None:
        self._session_scope = session_scope
        self._session = _session
        self._record_binding: CITask | None = None

    @property
    def _active_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("CI write requires an active repository transaction")
        return self._session

    @contextmanager
    def transaction(self):
        """One physical commit or rollback for all Use Case read/decide/write steps."""
        try:
            with self._session_scope() as session:
                with session.begin():
                    _set_repository_transaction_options(
                        session.connection(), read_only=False
                    )
                    yield SqlAlchemyCiStateRepository(
                        self._session_scope, _session=session
                    )
        except SQLAlchemyError:
            raise PersistenceUnavailableError(_OPERATION_ERROR) from None

    def load_for_tracking(self, task_id: str) -> CiTrackingSnapshot:
        """Lock Task before binding; keep both locked until the Use Case exits."""
        session = self._active_session
        task = session.get(Task, task_id, with_for_update=True)
        # Fail closed when authorization is absent; no ORM entity escapes.
        if task is None:
            return CiTrackingSnapshot(None, None, None, None, None)
        binding = session.get(CITask, task_id, with_for_update=True)
        return CiTrackingSnapshot(
            task.status,
            task.repository,
            task.current_head_sha,
            binding.pr_number if binding is not None else None,
            binding.head_sha if binding is not None else None,
        )

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None:
        """Persist a binding authorized by TrackCiTaskUseCase in this transaction."""
        self._active_session.add(
            CITask(
                task_id=task_id,
                pr_number=pr_number,
                head_sha=head_sha,
                verdict=CiVerdict.BLOCKED,
            )
        )

    def load_for_record(self, task_id: str) -> CiRecordSnapshot:
        """Lock Task then binding and read same-SHA failure before inserting."""
        session = self._active_session
        task = session.get(Task, task_id, with_for_update=True)
        binding = session.get(CITask, task_id, with_for_update=True)
        self._record_binding = binding
        if task is None or binding is None:
            return CiRecordSnapshot(
                task.repository if task is not None else None,
                task.current_head_sha if task is not None else None,
                binding.pr_number if binding is not None else None,
                binding.head_sha if binding is not None else None,
                False,
            )
        failure = (
            select(CIObservation.id)
            .where(
                CIObservation.task_id == task_id,
                CIObservation.head_sha == binding.head_sha,
                CIObservation.verdict == CiVerdict.FAILURE,
            )
            .exists()
        )
        return CiRecordSnapshot(
            task.repository,
            task.current_head_sha,
            binding.pr_number,
            binding.head_sha,
            bool(session.scalar(select(failure))),
        )

    def record(self, evidence: CiEvidence, verdict: str) -> None:
        """Append trusted evidence and update locked binding, no policy decisions."""
        session = self._active_session
        binding = self._record_binding
        if binding is None:
            raise RuntimeError("CI evidence write requires loaded binding")
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
        binding.verdict = verdict

    def inbox_delivery_exists(self, delivery_id: str) -> bool:
        """Lock inbox row for ACK; missing rows are judged in Application."""
        return (
            self._active_session.scalar(
                select(WebhookInboxRow.delivery_id)
                .where(WebhookInboxRow.delivery_id == delivery_id)
                .with_for_update(read=True, key_share=True)
            )
            is not None
        )

    def mark_processed(self, delivery_id: str) -> None:
        """Idempotently persist ACK of an already authorized inbox delivery."""
        processed = CIProcessedDelivery.__table__
        self._active_session.execute(
            insert(processed)
            .values(delivery_id=delivery_id)
            .on_conflict_do_nothing(index_elements=[processed.c.delivery_id])
        )
