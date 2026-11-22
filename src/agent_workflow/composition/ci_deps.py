"""Feature-local dependency assembly for Controller CI persistence."""

from contextlib import AbstractContextManager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from agent_workflow.application.ci.ports import ICiChecksReader
from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
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
from agent_workflow.storage.postgres_connection import PostgresFactory


@dataclass(frozen=True)
class PostgresCiStateDependencies:
    """Composition-owned dependency bundle; it exposes no runtime facade."""

    factory: PostgresFactory
    reader: SqlAlchemyCiStateReader
    repository: SqlAlchemyCiStateRepository
    tracker: TrackCiTaskUseCase
    recorder: RecordCiEvidenceUseCase
    acknowledger: AcknowledgeCiEventUseCase


def create_ci_state_dependencies(
    factory: PostgresFactory,
) -> PostgresCiStateDependencies:
    """Assemble CI reader/repository without opening a Session.

    Resolve the session provider on each invocation so operator scopes and
    post-composition test replacements preserve the existing late binding.
    """

    def session_scope() -> AbstractContextManager[Session]:
        return factory.get_scoped_session()

    provider: SessionScope = session_scope
    reader = SqlAlchemyCiStateReader(provider)
    repository = SqlAlchemyCiStateRepository(provider)
    return PostgresCiStateDependencies(
        factory=factory,
        reader=reader,
        repository=repository,
        tracker=TrackCiTaskUseCase(repository),
        recorder=RecordCiEvidenceUseCase(repository),
        acknowledger=AcknowledgeCiEventUseCase(repository),
    )


def create_ci_pass_use_case(
    factory: PostgresFactory,
    checks: ICiChecksReader,
    *,
    installation_id: int,
) -> RunCiPassUseCase:
    """Assemble the entire guarded CI Application graph once at bootstrap."""
    state = create_ci_state_dependencies(factory)
    leadership = PostgresCiLeadershipScope(factory)
    reconciler = ReconcileCiUseCase(
        GuardedCiState(
            reader=state.reader,
            recorder=state.recorder,
            acknowledger=state.acknowledger,
            require_owner=leadership.require_owner,
        ),
        GuardedCiChecks(checks, leadership.require_owner),
        installation_id=installation_id,
    )
    return RunCiPassUseCase(reconciler, leadership)
