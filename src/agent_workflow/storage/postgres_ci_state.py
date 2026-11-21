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
from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.interfaces.ci.state_adapter import CiStateAdapter
from agent_workflow.storage.postgres_connection import PostgresFactory


class PostgresControllerCiState(CiStateAdapter):
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
        # Legacy identity is retained for Worker admission until its own cutover.
        self._factory = factory
        self._repository = repository
        # Normal PostgreSQL composition supplies all four Use Cases. This
        # fallback exists solely for older PostgresControllerCiState(factory)
        # callers and must not be replicated in new adapters.
        super().__init__(
            reader=reader,
            tracker=tracker if tracker is not None else TrackCiTaskUseCase(repository),
            recorder=recorder
            if recorder is not None
            else RecordCiEvidenceUseCase(repository),
            approval=approval
            if approval is not None
            else CheckCiApprovalUseCase(reader),
            acknowledger=(
                acknowledger
                if acknowledger is not None
                else AcknowledgeCiEventUseCase(repository)
            ),
        )
