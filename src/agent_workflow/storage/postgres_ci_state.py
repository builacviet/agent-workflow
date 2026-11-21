"""Compatibility facade for the PostgreSQL Controller CI state contract."""

from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.application.ci.use_cases.check_ci_approval import (
    CheckCiApprovalUseCase,
)
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.domain.ci_evidence import CiEvidence, WebhookNotification
from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.storage.postgres_connection import PostgresFactory


class PostgresControllerCiState:
    """Combine the separated reader and repository for existing callers."""

    def __init__(
        self,
        reader: SqlAlchemyCiStateReader | PostgresFactory | None = None,
        repository: SqlAlchemyCiStateRepository | None = None,
        *,
        factory: PostgresFactory | None = None,
        tracker: TrackCiTaskUseCase | None = None,
        recorder: RecordCiEvidenceUseCase | None = None,
        approval: CheckCiApprovalUseCase | None = None,
        acknowledger: AcknowledgeCiEventUseCase | None = None,
    ) -> None:
        if isinstance(reader, PostgresFactory):
            if repository is not None or factory is not None:
                raise TypeError("factory compatibility construction takes one value")
            factory = reader
            reader = None
        if reader is None and repository is None and factory is not None:
            # Legacy construction: PostgresControllerCiState(factory=factory).
            session_scope = factory.get_scoped_session
            reader = SqlAlchemyCiStateReader(session_scope)
            repository = SqlAlchemyCiStateRepository(session_scope)
        if reader is None or repository is None:
            raise TypeError("CI state reader and repository are required")
        self._reader = reader
        self._repository = repository
        self._factory = factory
        # Composition supplies the Use Cases; legacy constructors retain wiring.
        self._tracker = tracker or TrackCiTaskUseCase(repository)
        self._recorder = recorder or RecordCiEvidenceUseCase(repository)
        self._approval = approval or CheckCiApprovalUseCase(reader)
        self._acknowledger = acknowledger or AcknowledgeCiEventUseCase(repository)

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None:
        self._tracker.execute(task_id, pr_number, head_sha)

    def tracked(self, limit: int = 32) -> list[dict]:
        return self._reader.tracked(limit)

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        return self._reader.previous_failure(task_id, head_sha)

    def record(self, evidence: CiEvidence) -> None:
        self._recorder.execute(evidence)

    def approved(self, task_id: str, head_sha: str) -> bool:
        return self._approval.execute(task_id, head_sha)

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        return self._reader.pending_events(limit)

    def mark_processed(self, delivery_id: str) -> None:
        self._acknowledger.execute(delivery_id)
