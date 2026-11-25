"""Layer: Composition
Responsibility: Assemble PostgreSQL Controller dependencies on a shared open factory.
Dependencies: Application operations and PostgreSQL adapters.
"""

from dataclasses import dataclass

from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.composition.ci_deps import PostgresCiWriteScope
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.infrastructure.database.repositories.dispatch_ledger import (
    PostgresDispatchLedger,
)
from agent_workflow.infrastructure.database.session_factory import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)


@dataclass(frozen=True)
class ControllerDependencies:
    handoff_provider: PostgresHandoffProvider
    dispatch: PostgresDispatchLedger
    ci_tracking: TrackCiTaskUseCase
    ci_write_scope: PostgresCiWriteScope
    ci_factory: PostgresFactory
    leadership: PostgresLeadershipLock


def compose_postgres_controller(factory: PostgresFactory) -> ControllerDependencies:
    """Borrow an already opened factory; the caller retains its entire lifetime.

    Only adapter and lock handles are constructed. In particular, no advisory
    session is checked out until a future, explicitly guarded caller acquires it.
    """
    if not isinstance(factory, PostgresFactory):
        raise TypeError("an opened PostgresFactory is required")
    if getattr(factory, "_closed", True):
        raise PostgresUnavailableError("PostgreSQL factory is closed")

    ci_write_scope = PostgresCiWriteScope(factory)
    return ControllerDependencies(
        handoff_provider=PostgresHandoffProvider(factory),
        dispatch=PostgresDispatchLedger(factory, role="dev", phase="code"),
        ci_tracking=TrackCiTaskUseCase(ci_write_scope),
        ci_write_scope=ci_write_scope,
        ci_factory=factory,
        leadership=PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER),
    )
