"""Wire the PostgreSQL dispatch pass and preserve the existing runner entry."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from agent_workflow.application.dispatch.ports import (
    DispatchExecution,
    IDispatchRepository,
)
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.composition.postgres_controller import ControllerDependencies
from agent_workflow.domain.service import WorkflowService


def run_controller_once(
    dependencies: ControllerDependencies,
    assignment: WorkflowService,
    task_ids: set[str],
    execute: DispatchExecution,
    timeout: int = 1800,
    *,
    on_finished: Callable[[str], None] | None = None,
) -> bool:
    """Compatibility entry for callers that supply their own dependencies."""
    if assignment.repository is not dependencies.workflow:
        raise TypeError(
            "Controller assignment must use the composed PostgreSQL workflow"
        )
    dispatch_pass = RunDispatchPassUseCase(
        dependencies.leadership,
        cast(IDispatchRepository, dependencies.dispatch),
        assignment,
        execute,
        on_finished=on_finished,
    )
    return dispatch_pass.execute(task_ids, timeout)
