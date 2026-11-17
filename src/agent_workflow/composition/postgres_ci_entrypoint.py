"""Own PostgreSQL CI reconciliation and its Controller-only credentials."""

import time
from pathlib import Path
from typing import Callable

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
    installation_id, checks = _controller_checks(config, checks)
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


def run_postgres_ci_watch(
    config: dict,
    app_config: AppConfig,
    *,
    checks: ReadOnlyChecks | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Poll with a fresh guarded leadership acquisition for each CI pass."""
    installation_id, checks = _controller_checks(config, checks)
    delay = 2
    startup = True
    with PostgresFactory.open(app_config) as factory:
        watcher = compose_postgres_controller(factory).ci_watcher
        while stop is None or not stop():
            observations = run_ci_watcher_once(
                watcher, checks, installation_id=installation_id, startup=startup
            )
            if observations is None:
                raise WorkflowError("PostgreSQL CI watcher leadership unavailable")
            startup = False
            if any(e.verdict == "BLOCKED" for e in observations):
                delay = min(60, delay * 2)
            else:
                delay = 2
            if stop is not None and stop():
                break
            sleep(delay)


def _controller_checks(
    config: dict, checks: ReadOnlyChecks | None
) -> tuple[int, ReadOnlyChecks]:
    """Validate the separate Controller App before opening the database."""
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
    return installation_id, checks
