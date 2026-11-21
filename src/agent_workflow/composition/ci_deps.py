"""Feature-local dependency assembly for Controller CI persistence."""

from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from agent_workflow.application.ci.ports import ICiChecksReader
from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.application.ci.use_cases.check_ci_approval import (
    CheckCiApprovalUseCase,
)
from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.application.ci.use_cases.run_ci_pass import RunCiPassUseCase
from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.infrastructure.ci.guarded_adapters import (
    GuardedCiChecks,
    GuardedCiState,
)
from agent_workflow.infrastructure.ci.leadership_scope import PostgresCiLeadershipScope
from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SessionScope,
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.interfaces.ci.state_adapter import CiStateAdapter
from agent_workflow.storage.postgres_connection import PostgresFactory


class ComposedCiState(CiStateAdapter):
    """Controller-facing adapter with composition-owned factory provenance.

    Neither Application nor Interfaces receives the factory. The Worker uses
    this identity to reject dependencies from different open factories.
    """

    def __init__(
        self,
        factory: PostgresFactory,
        *,
        reader: SqlAlchemyCiStateReader,
        repository: SqlAlchemyCiStateRepository,
        tracker: TrackCiTaskUseCase,
        recorder: RecordCiEvidenceUseCase,
        approval: CheckCiApprovalUseCase,
        acknowledger: AcknowledgeCiEventUseCase,
    ) -> None:
        self._factory = factory
        self._repository = repository
        super().__init__(
            reader=reader,
            tracker=tracker,
            recorder=recorder,
            approval=approval,
            acknowledger=acknowledger,
        )


def create_ci_state(factory: PostgresFactory) -> ComposedCiState:
    """Assemble CI reader/repository without opening a Session.

    Resolve the session provider on each invocation so operator scopes and
    post-composition test replacements preserve the existing late binding.
    """

    def session_scope() -> AbstractContextManager[Session]:
        return factory.get_scoped_session()

    provider: SessionScope = session_scope
    reader = SqlAlchemyCiStateReader(provider)
    repository = SqlAlchemyCiStateRepository(provider)
    return ComposedCiState(
        factory,
        reader=reader,
        repository=repository,
        tracker=TrackCiTaskUseCase(repository),
        recorder=RecordCiEvidenceUseCase(repository),
        approval=CheckCiApprovalUseCase(reader),
        acknowledger=AcknowledgeCiEventUseCase(repository),
    )


def create_ci_pass_use_case(
    factory: PostgresFactory,
    checks: ICiChecksReader,
    *,
    installation_id: int,
) -> RunCiPassUseCase:
    """Assemble the entire guarded CI Application graph once at bootstrap."""
    state = create_ci_state(factory)
    leadership = PostgresCiLeadershipScope(factory)
    reconciler = ReconcileCiUseCase(
        GuardedCiState(state, leadership.require_owner),
        GuardedCiChecks(checks, leadership.require_owner),
        installation_id=installation_id,
    )
    return RunCiPassUseCase(reconciler, leadership)
