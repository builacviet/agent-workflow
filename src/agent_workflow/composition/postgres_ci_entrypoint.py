"""Own PostgreSQL CI reconciliation and its Controller-only credentials."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_workflow.app_config import AppConfig
from agent_workflow.application.ci.ports import ICiChecksReader
from agent_workflow.application.exceptions import (
    ApplicationError,
    InvalidApplicationRequestError,
)
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.postgres_leader_runner import run_ci_watcher_once
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
    """Own CI credentials, PostgreSQL lifetime and leader-fenced reconciliation.

    The yielded callback is an Application operation bound to a live composition.
    It performs one pass only; the caller owns output, retry timing and shutdown.
    """
    installation_id, checks = _controller_checks(config, checks)
    with PostgresFactory.open(app_config) as factory:

        def reconcile(request: ci_cli.CiRunRequest) -> ci_cli.CiRunResponse:
            dependencies = compose_postgres_controller(factory).ci_watcher
            observations = run_ci_watcher_once(
                dependencies,
                checks,
                installation_id=installation_id,
                startup=request.startup,
            )
            if observations is None:
                raise ApplicationError("PostgreSQL CI watcher leadership unavailable")
            return ci_cli.present_ci_observations(observations)

        yield reconcile


def run_postgres_ci_once(
    config: dict, app_config: AppConfig, *, checks: ICiChecksReader | None = None
) -> None:
    """Compatibility entrypoint; CI operator output belongs to Interfaces."""
    with compose_ci_execution(config, app_config, checks=checks) as reconcile:
        ci_cli.run_ci_once(reconcile)


def run_postgres_ci_watch(
    config: dict,
    app_config: AppConfig,
    *,
    checks: ICiChecksReader | None = None,
    sleep: Callable[[float], None] = ci_cli.sleep,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Compatibility entrypoint; polling and shutdown belong to Interfaces."""
    with compose_ci_execution(config, app_config, checks=checks) as reconcile:
        ci_cli.run_ci_watch(reconcile, sleep=sleep, stop=stop)


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
