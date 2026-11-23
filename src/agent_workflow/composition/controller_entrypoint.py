"""Bind PostgreSQL providers to the Controller CLI."""

from agent_workflow.composition.postgres_ci_entrypoint import compose_ci_execution
from agent_workflow.composition.postgres_dispatch_entrypoint import (
    compose_dispatch_execution,
)
from agent_workflow.infrastructure.logging.json_formatter import ci_log_scope
from agent_workflow.interfaces.cli.controller_cli import run_controller_cli


def main() -> None:
    run_controller_cli(
        compose_ci=compose_ci_execution,
        compose_dispatch=compose_dispatch_execution,
        log_scope=ci_log_scope,
    )


if __name__ == "__main__":
    main()
