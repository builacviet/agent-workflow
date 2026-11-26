"""Layer: Composition
Responsibility: Resolve only the selected Controller dependency scope.
Dependencies: CI and Dispatch Composition providers and infrastructure logging.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from agent_workflow.infrastructure.logging.json_formatter import (
    ci_log_scope as ci_log_scope,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from agent_workflow.app_config import AppConfig
    from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
    from agent_workflow.application.dispatch.run_dispatch_pass import (
        RunDispatchPassUseCase,
    )


def compose_ci(
    config: dict, app_config: AppConfig
) -> AbstractContextManager[ReconcileCiUseCase]:
    return import_module("agent_workflow.composition.ci_deps").compose_ci_execution(
        config, app_config
    )


def compose_dispatch(
    config: dict, app_config: AppConfig
) -> AbstractContextManager[RunDispatchPassUseCase]:
    deps = import_module("agent_workflow.composition.dispatch_deps")
    return deps.compose_dispatch_execution(config, app_config)
