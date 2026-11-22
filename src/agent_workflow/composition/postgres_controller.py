"""Prepare PostgreSQL Controller and CI watcher dependencies without running them."""

from dataclasses import dataclass

from agent_workflow.application.ci.ports import ICiStateReader
from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.composition.ci_deps import (
    PostgresCiStateDependencies,
    create_ci_state_dependencies,
)
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
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
    ci_state: PostgresCiStateDependencies
    leadership: PostgresLeadershipLock


@dataclass(frozen=True)
class PostgresCiWatcherDependencies:
    reader: ICiStateReader
    recorder: RecordCiEvidenceUseCase
    acknowledger: AcknowledgeCiEventUseCase
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

    ci_state = create_ci_state_dependencies(factory)
    return PostgresControllerComposition(
        controller=PostgresControllerDependencies(
            workflow=PostgresWorkflowRepository(factory),
            dispatch=PostgresDispatchLedger(factory, role="dev", phase="code"),
            ci_state=ci_state,
            leadership=PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER),
        ),
        ci_watcher=PostgresCiWatcherDependencies(
            reader=ci_state.reader,
            recorder=ci_state.recorder,
            acknowledger=ci_state.acknowledger,
            leadership=PostgresLeadershipLock(factory, LeadershipDomain.CI_WATCHER),
        ),
    )
