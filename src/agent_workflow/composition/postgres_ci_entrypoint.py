"""Own PostgreSQL CI reconciliation and its Controller-only credentials."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_workflow.app_config import AppConfig
from agent_workflow.application.ci.ports import ICiChecksReader
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.composition.ci_deps import create_ci_pass_use_case
from agent_workflow.integrations.github_app_checks import (
    ControllerAppToken,
    ControllerChecksClient,
)
from agent_workflow.interfaces.cli import ci_controller as ci_cli
from agent_workflow.storage.postgres_connection import PostgresFactory


@contextmanager
def compose_ci_execution(
    config: dict,
    app_config: AppConfig,
    *,
    checks: ICiChecksReader | None = None,
) -> Iterator[ci_cli.CiRunOperation]:
    """Build CI dependencies once, inject Use Cases and own resource lifetime.

    The yielded Interface adapter invokes Application directly on every pass.
    Composition never re-enters the request execution path.
    """
    installation_id, checks = _controller_checks(config, checks)
    with PostgresFactory.open(app_config) as factory:
        use_case = create_ci_pass_use_case(
            factory, checks, installation_id=installation_id
        )
        controller = ci_cli.CiController(use_case)
        yield controller.reconcile


def _controller_checks(
    config: dict, checks: ICiChecksReader | None
) -> tuple[int, ICiChecksReader]:
    """Validate the separate Controller App before opening the database."""
    gate = config.get("controller_github_app")
    if not isinstance(gate, dict):
        raise InvalidApplicationRequestError(
            "separate read-only Controller GitHub App config required"
        )
    app_id, installation_id, pem = (
        gate.get("app_id"),
        gate.get("installation_id"),
        gate.get("pem"),
    )
    if type(app_id) is not int or type(installation_id) is not int or not pem:
        raise InvalidApplicationRequestError("invalid Controller GitHub App config")
    if config.get("pem") and Path(config["pem"]).resolve() == Path(pem).resolve():
        raise InvalidApplicationRequestError(
            "Controller GitHub App must not use worker App PEM"
        )

    if checks is None:
        token = ControllerAppToken(
            app_id=app_id, installation_id=installation_id, pem_path=pem
        )
        checks = ControllerChecksClient(
            installation_id=installation_id, token_provider=token
        )
    return installation_id, checks
