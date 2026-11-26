"""Layer: Interfaces/CLI
Responsibility: Start the Controller with the configured mode providers.
Dependencies: Controller CLI, AppConfig and Controller Composition providers.
"""

from agent_workflow.app_config import AppConfig
from agent_workflow.composition.controller_deps import (
    ci_log_scope,
    compose_ci,
    compose_dispatch,
)
from agent_workflow.interfaces.cli.controller_cli import run_controller_cli


def main() -> None:
    run_controller_cli(
        compose_ci=compose_ci,
        compose_dispatch=compose_dispatch,
        load_app_config=AppConfig.from_env,
        log_scope=ci_log_scope,
    )


if __name__ == "__main__":
    main()
