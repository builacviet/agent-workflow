"""Wire a dispatch pass to the Controller's shared PostgreSQL dependencies."""

from collections.abc import Callable
from typing import cast

from agent_workflow.app_config import AppConfig
from agent_workflow.application.dispatch.ports import (
    CoderAssignment,
    DispatchClaim,
    IDispatchRepository,
)
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.worker_deps import compose_postgres_worker
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.postgres_connection import PostgresFactory


def create_dispatch_use_case(
    config: dict, app_config: AppConfig, factory: PostgresFactory
) -> RunDispatchPassUseCase:
    dependencies = compose_postgres_controller(factory)
    service = WorkflowService(
        repository=dependencies.workflow, require_ci=app_config.ci_gate_required
    )
    runtime = None

    def execute_worker(
        claim: DispatchClaim,
        context: CoderAssignment,
        guard: Callable[[], None],
    ) -> str:
        nonlocal runtime
        guard()
        runtime = compose_postgres_worker(
            config, service, dependencies, leadership_guard=guard
        )
        guard()
        return runtime.execute(claim, context)

    def finished(dispatch_id: str) -> None:
        if runtime is None:
            raise WorkflowError("missing dispatched Worker for terminal cleanup")
        runtime.release_engine(dispatch_id)

    if service.repository is not dependencies.workflow:
        raise TypeError(
            "Controller assignment must use the composed PostgreSQL workflow"
        )
    return RunDispatchPassUseCase(
        dependencies.leadership,
        cast(IDispatchRepository, dependencies.dispatch),
        service,
        execute_worker,
        on_finished=finished,
    )
