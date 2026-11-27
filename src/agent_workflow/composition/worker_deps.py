"""Layer: Composition
Responsibility: Compose a trusted Worker from Controller dependencies.
Dependencies: Application operations and PostgreSQL adapters.
"""

from collections.abc import Callable

from agent_workflow.app_config import AppConfig
from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.composition.ci_deps import PostgresCiWriteScope
from agent_workflow.composition.postgres_controller import ControllerDependencies
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.infrastructure.database.repositories.dispatch_ledger import (
    PostgresDispatchLedger,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.infrastructure.runtime.registry import RuntimeRegistry
from agent_workflow.infrastructure.runtime.sandbox import SandboxManager
from agent_workflow.runners.coder_runtime import CoderRuntime
from agent_workflow.sandbox_state_paths import validate_sandbox_state_dir


def compose_postgres_worker(
    config: dict,
    handoff_provider: PostgresHandoffProvider,
    dependencies: ControllerDependencies,
    *,
    leadership_guard: Callable[[], None],
    registry: RuntimeRegistry,
    app_config: AppConfig,
) -> CoderRuntime:
    """Borrow the Controller's adapters and its raising guard for this claim.

    Constructing the runtime performs no ownership check or Worker execution.
    The Controller caller owns the factory and the leadership session.
    """
    if not isinstance(dependencies, ControllerDependencies):
        raise TypeError("PostgreSQL Controller dependencies are required")
    dispatch = dependencies.dispatch
    ci_tracking = dependencies.ci_tracking
    ci_scope = dependencies.ci_write_scope
    ci_factory = dependencies.ci_factory
    leadership = dependencies.leadership
    if (
        type(handoff_provider) is not PostgresHandoffProvider
        or handoff_provider is not dependencies.handoff_provider
    ):
        raise TypeError("Worker requires the composed PostgreSQL handoff provider")
    if (
        not isinstance(dispatch, PostgresDispatchLedger)
        or (dispatch.role, dispatch.phase) != ("dev", "code")
        or not isinstance(ci_tracking, TrackCiTaskUseCase)
        or type(ci_scope) is not PostgresCiWriteScope
        or ci_tracking._scope is not ci_scope
        or not isinstance(ci_factory, PostgresFactory)
        or not isinstance(leadership, PostgresLeadershipLock)
        or leadership._key["domain"] != LeadershipDomain.CONTROLLER
        or not callable(leadership_guard)
    ):
        raise TypeError(
            "Worker requires PostgreSQL dispatch, CI state and Controller guard"
        )

    factory = handoff_provider.factory
    if (
        not isinstance(factory, PostgresFactory)
        or factory._closed
        or dispatch.factory is not factory
        or ci_factory is not factory
        or ci_scope.factory is not factory
        or leadership._factory is not factory
    ):
        raise TypeError("Worker dependencies must share the open Controller factory")
    if leadership._retired:
        raise PermissionError("Worker requires active Controller leadership")

    def guard() -> bool:
        # The runner guard raises on loss. Also verify the borrowed lock itself
        # so a callback for another ownership lifetime cannot authorize work.
        return (
            not factory._closed
            and leadership_guard() is None
            and leadership.is_held() is True
        )

    if type(app_config) is not AppConfig:
        raise TypeError("Worker requires explicit AppConfig")
    sandbox = SandboxManager(
        fence_dir=validate_sandbox_state_dir(app_config.sandbox_state_dir)
    )
    return CoderRuntime(
        config,
        handoff_provider,
        registry=registry,
        sandbox=sandbox,
        dispatch=dispatch,
        ci=ci_tracking,
        leadership_guard=guard,
    )
