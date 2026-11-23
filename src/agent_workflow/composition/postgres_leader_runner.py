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


def compose_dispatch_pass(
    dependencies: ControllerDependencies,
    assignment: WorkflowService,
    execute: Callable[[dict, dict, Callable[[], None]], str],
    *,
    on_finished: Callable[[str], None] | None = None,
) -> RunDispatchPassUseCase:
    """Bind typed ports to the existing dictionary-based ledger and callback."""
    if assignment.repository is not dependencies.workflow:
        raise TypeError(
            "Controller assignment must use the composed PostgreSQL workflow"
        )
    return RunDispatchPassUseCase(
        dependencies.leadership,
        cast(IDispatchRepository, dependencies.dispatch),
        assignment,
        cast(DispatchExecution, execute),
        on_finished=on_finished,
    )


def run_controller_once(
    dependencies: ControllerDependencies,
    assignment: WorkflowService,
    task_ids: set[str],
    execute: Callable[[dict, dict, Callable[[], None]], str],
    timeout: int = 1800,
    *,
    on_finished: Callable[[str], None] | None = None,
) -> bool:
    """Compatibility entry for callers that supply their own dependencies."""
    return compose_dispatch_pass(
        dependencies, assignment, execute, on_finished=on_finished
    ).execute(task_ids, timeout)
