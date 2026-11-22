"""Feature-local CI persistence assembly; database specifics stay in Composition."""

from contextlib import AbstractContextManager

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


def create_ci_state_adapters(
    factory: PostgresFactory,
) -> tuple[SqlAlchemyCiStateReader, SqlAlchemyCiStateRepository]:
    """Wire adapters without a technology-specific dependency bundle.

    Resolve sessions at invocation time for correct late binding, without
    acquiring a database session during bootstrap.
    """

    def session_scope() -> AbstractContextManager[Session]:
        return factory.get_scoped_session()

    provider: SessionScope = session_scope
    return SqlAlchemyCiStateReader(provider), SqlAlchemyCiStateRepository(provider)


def create_ci_pass_use_case(
    factory: PostgresFactory,
    checks: ICiChecksReader,
    *,
    installation_id: int,
) -> RunCiPassUseCase:
    """Assemble guarded CI Application graph once at bootstrap."""
    reader, repository = create_ci_state_adapters(factory)
    leadership = PostgresCiLeadershipScope(factory)
    reconciler = ReconcileCiUseCase(
        GuardedCiState(
            reader=reader,
            recorder=RecordCiEvidenceUseCase(repository),
            acknowledger=AcknowledgeCiEventUseCase(repository),
            require_owner=leadership.require_owner,
        ),
        GuardedCiChecks(checks, leadership.require_owner),
        installation_id=installation_id,
    )
    return RunCiPassUseCase(reconciler, leadership)
