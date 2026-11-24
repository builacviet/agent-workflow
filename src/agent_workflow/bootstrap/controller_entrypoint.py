"""Layer: Bootstrap
Responsibility: Start the Controller CLI and wire mode-specific providers.
Dependencies: AppConfig, Controller CLI, PostgreSQL Composition and logging.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from agent_workflow.app_config import AppConfig
from agent_workflow.infrastructure.logging.json_formatter import ci_log_scope
from agent_workflow.interfaces.cli.controller_cli import run_controller_cli

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from agent_workflow.application.dispatch.run_dispatch_pass import (
        RunDispatchPassUseCase,
    )
    from agent_workflow.interfaces.cli.ci_controller import CiRunOperation, ComposeCi
    from agent_workflow.interfaces.cli.dispatch_controller import ComposeDispatch


def _compose_ci(
    config: dict, app_config: AppConfig
) -> AbstractContextManager[CiRunOperation]:
    compose: ComposeCi = import_module(
        "agent_workflow.composition.postgres_ci_entrypoint"
    ).compose_ci_execution
    return compose(config, app_config)


def _compose_dispatch(
    config: dict, app_config: AppConfig
) -> AbstractContextManager[RunDispatchPassUseCase]:
    compose: ComposeDispatch = import_module(
        "agent_workflow.composition.postgres_dispatch_entrypoint"
    ).compose_dispatch_execution
    return compose(config, app_config)


def main() -> None:
    run_controller_cli(
        compose_ci=_compose_ci,
        compose_dispatch=_compose_dispatch,
        load_app_config=AppConfig.from_env,
        log_scope=ci_log_scope,
    )


if __name__ == "__main__":
    main()
