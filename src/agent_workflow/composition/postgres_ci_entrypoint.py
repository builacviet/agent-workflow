"""Own one PostgreSQL CI reconciliation and its Controller-only credentials."""

from pathlib import Path

from agent_workflow.app_config import AppConfig
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.postgres_leader_runner import run_ci_watcher_once
from agent_workflow.integrations.ci_gate import ReadOnlyChecks
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.integrations.github_app_checks import (
    ControllerAppToken,
    ControllerChecksClient,
)
from agent_workflow.storage.postgres_connection import PostgresFactory


def run_postgres_ci_once(
    config: dict, app_config: AppConfig, *, checks: ReadOnlyChecks | None = None
) -> None:
    """Run one guarded CI pass; a denied leader performs no reconciliation."""
    gate = config.get("controller_github_app")
    if not isinstance(gate, dict):
        raise WorkflowError("separate read-only Controller GitHub App config required")
    app_id, installation_id, pem = (
        gate.get("app_id"),
        gate.get("installation_id"),
        gate.get("pem"),
    )
    if type(app_id) is not int or type(installation_id) is not int or not pem:
        raise WorkflowError("invalid Controller GitHub App config")
    if config.get("pem") and Path(config["pem"]).resolve() == Path(pem).resolve():
        raise WorkflowError("Controller GitHub App must not use worker App PEM")

    if checks is None:
        token = ControllerAppToken(
            app_id=app_id, installation_id=installation_id, pem_path=pem
        )
        checks = ControllerChecksClient(
            installation_id=installation_id, token_provider=token
        )
    with PostgresFactory.open(app_config) as factory:
        watcher = compose_postgres_controller(factory).ci_watcher
        observations = run_ci_watcher_once(
            watcher, checks, installation_id=installation_id, startup=True
        )
        if observations is None:
            raise WorkflowError("PostgreSQL CI watcher leadership unavailable")
        print(
            "CI reconciliation: " + str(len(observations)) + " tracked observations",
            flush=True,
        )
