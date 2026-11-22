"""Inert PostgreSQL state wiring for the trusted Worker runtime."""

from collections.abc import Callable, Iterable

from agent_workflow.composition.ci_deps import PostgresCiStateDependencies
from agent_workflow.composition.postgres_controller import (
    PostgresControllerDependencies,
)
from agent_workflow.domain.service import WorkflowService
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.runners.coder_runtime import CoderRuntime
from agent_workflow.runners.runtime_plugins import AgentRuntimePlugin
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_dispatch import PostgresDispatchLedger
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


def compose_postgres_worker(
    config: dict,
    service: WorkflowService,
    dependencies: PostgresControllerDependencies,
    *,
    leadership_guard: Callable[[], None],
    plugins: Iterable[AgentRuntimePlugin] = (),
) -> CoderRuntime:
    """Borrow the Controller's adapters and its raising guard for this claim.

    Constructing the runtime performs no ownership check or Worker execution.
    The Controller caller owns the factory and the leadership session.
    """
    if not isinstance(dependencies, PostgresControllerDependencies):
        raise TypeError("PostgreSQL Controller dependencies are required")
    workflow = dependencies.workflow
    dispatch = dependencies.dispatch
    ci_state = dependencies.ci_state
    leadership = dependencies.leadership
    if (
        not isinstance(workflow, PostgresWorkflowRepository)
        or not isinstance(service, WorkflowService)
        or service.repository is not workflow
    ):
        raise TypeError("Worker requires the composed PostgreSQL workflow")
    if (
        not isinstance(dispatch, PostgresDispatchLedger)
        or (dispatch.role, dispatch.phase) != ("dev", "code")
        or not isinstance(ci_state, PostgresCiStateDependencies)
        or not isinstance(leadership, PostgresLeadershipLock)
        or leadership._key["domain"] != LeadershipDomain.CONTROLLER
        or not callable(leadership_guard)
    ):
        raise TypeError(
            "Worker requires PostgreSQL dispatch, CI state and Controller guard"
        )

    factory = workflow.factory
    if (
        not isinstance(factory, PostgresFactory)
        or factory._closed
        or dispatch.factory is not factory
        or ci_state.factory is not factory
        or leadership._factory is not factory
    ):
        raise TypeError("Worker dependencies must share the open Controller factory")

    def guard() -> bool:
        # The runner guard raises on loss. Also verify the borrowed lock itself
        # so a callback for another ownership lifetime cannot authorize work.
        return (
            not factory._closed
            and leadership_guard() is None
            and leadership.is_held() is True
        )

    return CoderRuntime(
        config,
        service,
        plugins=plugins,
        dispatch=dispatch,
        ci=ci_state.tracker,
        leadership_guard=guard,
    )
