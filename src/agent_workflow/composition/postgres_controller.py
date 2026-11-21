"""Prepare PostgreSQL Controller and CI watcher dependencies without running them."""

from dataclasses import dataclass

from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.storage.postgres_ci_state import PostgresControllerCiState
from agent_workflow.storage.postgres_connection import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.storage.postgres_dispatch import PostgresDispatchLedger
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


@dataclass(frozen=True)
class PostgresControllerDependencies:
    workflow: PostgresWorkflowRepository
    dispatch: PostgresDispatchLedger
    ci_state: PostgresControllerCiState
    leadership: PostgresLeadershipLock


@dataclass(frozen=True)
class PostgresCiWatcherDependencies:
    ci_state: PostgresControllerCiState
    leadership: PostgresLeadershipLock


@dataclass(frozen=True)
class PostgresControllerComposition:
    controller: PostgresControllerDependencies
    ci_watcher: PostgresCiWatcherDependencies


def compose_postgres_controller(
    factory: PostgresFactory,
) -> PostgresControllerComposition:
    """Borrow an already opened factory; the caller retains its entire lifetime.

    Only adapter and lock handles are constructed. In particular, no advisory
    session is checked out until a future, explicitly guarded caller acquires it.
    """
    if not isinstance(factory, PostgresFactory):
        raise TypeError("an opened PostgresFactory is required")
    if getattr(factory, "_closed", True):
        raise PostgresUnavailableError("PostgreSQL factory is closed")

    session_scope = factory.get_scoped_session
    ci_state = PostgresControllerCiState(
        reader=SqlAlchemyCiStateReader(session_scope),
        repository=SqlAlchemyCiStateRepository(session_scope),
        factory=factory,
    )
    return PostgresControllerComposition(
        controller=PostgresControllerDependencies(
            workflow=PostgresWorkflowRepository(factory),
            dispatch=PostgresDispatchLedger(factory, role="dev", phase="code"),
            ci_state=ci_state,
            leadership=PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER),
        ),
        ci_watcher=PostgresCiWatcherDependencies(
            ci_state=ci_state,
            leadership=PostgresLeadershipLock(factory, LeadershipDomain.CI_WATCHER),
        ),
    )
