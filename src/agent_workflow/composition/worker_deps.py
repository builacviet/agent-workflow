"""Compose a trusted Worker runtime from already assembled dependencies."""

from collections.abc import Callable, Iterable
from inspect import getclosurevars
from types import FunctionType

from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.composition.postgres_controller import (
    ControllerDependencies,
    compose_postgres_controller,
)
from agent_workflow.domain.service import WorkflowService
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.runners.coder_runtime import CoderRuntime
from agent_workflow.runners.runtime_plugins import AgentRuntimePlugin
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_dispatch import PostgresDispatchLedger
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


def _uses_controller_ci_factory(
    repository: SqlAlchemyCiStateRepository, factory: PostgresFactory
) -> bool:
    # The Controller supplies a deferred lambda. Verify its factory binding
    # without calling it or opening a session during composition.
    scope = repository._session_scope
    if (
        repository._session is not None
        or not isinstance(scope, FunctionType)
        or scope.__module__ != compose_postgres_controller.__module__
        or scope.__qualname__
        != f"{compose_postgres_controller.__qualname__}.<locals>.<lambda>"
    ):
        return False
    try:
        return getclosurevars(scope).nonlocals.get("factory") is factory
    except ValueError:
        return False


def compose_postgres_worker(
    config: dict,
    service: WorkflowService,
    dependencies: ControllerDependencies,
    *,
    leadership_guard: Callable[[], None],
    plugins: Iterable[AgentRuntimePlugin] = (),
) -> CoderRuntime:
    """Borrow the Controller's adapters and its raising guard for this claim.

    Constructing the runtime performs no ownership check or Worker execution.
    The Controller caller owns the factory and the leadership session.
    """
    if not isinstance(dependencies, ControllerDependencies):
        raise TypeError("PostgreSQL Controller dependencies are required")
    workflow = dependencies.workflow
    dispatch = dependencies.dispatch
    ci_tracking = dependencies.ci_tracking
    ci_repository = dependencies.ci_repository
    ci_factory = dependencies.ci_factory
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
        or not isinstance(ci_tracking, TrackCiTaskUseCase)
        or not isinstance(ci_repository, SqlAlchemyCiStateRepository)
        or ci_tracking._repository is not ci_repository
        or not isinstance(ci_factory, PostgresFactory)
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
        or ci_factory is not factory
        or not _uses_controller_ci_factory(ci_repository, factory)
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

    return CoderRuntime(
        config,
        service,
        plugins=plugins,
        dispatch=dispatch,
        ci=ci_tracking,
        leadership_guard=guard,
    )
