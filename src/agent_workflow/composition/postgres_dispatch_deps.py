"""Layer: Composition
Responsibility: Wire the dispatch pass to shared PostgreSQL dependencies.
Dependencies: Application operations and PostgreSQL adapters.
"""

from collections.abc import Callable
from typing import cast

from agent_workflow.app_config import AppConfig
from agent_workflow.application.dispatch.ports import CoderAssignment, DispatchClaim
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    DispatchAssignment,
    GetCoderAssignmentUseCase,
)
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.composition.worker_deps import compose_postgres_worker
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.integrations.github import WorkflowError


def create_dispatch_use_case(
    config: dict, app_config: AppConfig, factory: PostgresFactory
) -> RunDispatchPassUseCase:
    dependencies = compose_postgres_controller(factory)
    handoff_provider = dependencies.handoff_provider
    if (
        type(handoff_provider) is not PostgresHandoffProvider
        or handoff_provider.factory is not factory
    ):
        raise TypeError("Dispatch requires the composed PostgreSQL handoff provider")
    assignment = GetCoderAssignmentUseCase(create_get_review_context_use_case(factory))
    runtime = None

    def execute_worker(
        claim: DispatchClaim,
        context: CoderAssignment,
        guard: Callable[[], None],
    ) -> str:
        nonlocal runtime
        guard()
        runtime = compose_postgres_worker(
            config, handoff_provider, dependencies, leadership_guard=guard
        )
        guard()
        return runtime.execute_assignment(claim, cast(DispatchAssignment, context))

    def finished(dispatch_id: str) -> None:
        if runtime is None:
            raise WorkflowError("missing dispatched Worker for terminal cleanup")
        runtime.release_engine(dispatch_id)

    return RunDispatchPassUseCase(
        dependencies.leadership,
        dependencies.dispatch,
        assignment,
        execute_worker,
        on_finished=finished,
    )
