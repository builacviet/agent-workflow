"""Layer: Composition
Responsibility: Assemble CI readers, write scopes and watcher dependencies.
Dependencies: Application CI operations and PostgreSQL adapters.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from agent_workflow.application.ci.ports import (
    ICiBusinessWriteRepository,
    ICiChecksReader,
)
from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.infrastructure.ci.guarded_adapters import (
    GuardedCiChecks,
    GuardedCiReader,
)
from agent_workflow.infrastructure.ci.leadership_scope import PostgresCiLeadershipScope
from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SessionScope,
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True)
class PostgresCiWriteScope:
    """Construct a fresh shared-Session UoW and repository per invocation."""

    factory: PostgresFactory

    @contextmanager
    def __call__(self) -> Iterator[tuple[IUnitOfWork, ICiBusinessWriteRepository]]:
        with self.factory.get_scoped_session() as session:
            yield (
                SqlAlchemyUnitOfWork(session, profile="ci_write"),
                SqlAlchemyCiStateRepository(session=session),
            )


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


def create_ci_reconcile_use_case(
    factory: PostgresFactory,
    checks: ICiChecksReader,
    *,
    installation_id: int,
) -> ReconcileCiUseCase:
    """Assemble guarded CI Application graph once at bootstrap."""
    reader, repository = create_ci_state_adapters(factory)
    leadership = PostgresCiLeadershipScope(factory)
    reconciler = ReconcileCiUseCase(
        GuardedCiReader(reader=reader, require_owner=leadership.require_owner),
        GuardedCiChecks(checks, leadership.require_owner),
        recorder=RecordCiEvidenceUseCase(PostgresCiWriteScope(factory)),
        acknowledger=AcknowledgeCiEventUseCase(repository),
        leadership=leadership,
        installation_id=installation_id,
    )
    return reconciler
