"""Own the validated, one-shot PostgreSQL Dispatch dependency scope."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_workflow.app_config import AppConfig
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.composition.postgres_dispatch_deps import create_dispatch_use_case
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.integrations.github_app_checks import ControllerAppToken
from agent_workflow.runners.coder_runtime import validate_config
from agent_workflow.runners.runtime_plugins import CodexCLIPlugin
from agent_workflow.workers.trusted_git.app_auth import GitHubApp


@contextmanager
def compose_dispatch_execution(
    config: dict, app_config: AppConfig
) -> Iterator[RunDispatchPassUseCase]:
    """Preflight the trusted adapters and lend one factory to the use case."""
    _validate_worker_config(config)
    with PostgresFactory.open(app_config) as factory:
        yield create_dispatch_use_case(config, app_config, factory)


def _validate_worker_config(config: dict) -> None:
    """Validate local settings only: no database, Docker or GitHub requests."""
    try:
        validate_config(config, require_database=False)
        # This entrypoint installs only the existing trusted Codex plugin.
        # Reject unsupported selection before opening any database resources.
        if (
            config.get("runtime", "codex") != "codex"
            or (
                config.get("provider", CodexCLIPlugin.default_provider),
                config["model"],
                config.get("effort"),
            )
            not in CodexCLIPlugin.manifest.supported_combinations
        ):
            raise WorkflowError("unsupported Worker runtime selection")
        # Reuse the trusted Worker's credential checks without authenticating.
        worker = GitHubApp(
            config["app_id"], config["installation_id"], Path(config["pem"])
        )
        gate = config["controller_github_app"]
        controller = ControllerAppToken(
            app_id=gate["app_id"],
            installation_id=gate["installation_id"],
            pem_path=gate["pem"],
        )
        controller_pem = Path(gate["pem"]).resolve(strict=True)
        if (
            not controller_pem.is_file()
            or worker.pem == controller_pem
            or worker.app_id == controller.app_id
            or worker.installation_id == controller.installation_id
        ):
            raise WorkflowError("separate Controller and Worker credentials required")
        roots = [Path(config["scratch"]).resolve()] + [
            Path(task["worktree"]).resolve() for task in config["tasks"].values()
        ]
        if any(
            pem.is_relative_to(root)
            for pem in (worker.pem, controller_pem)
            for root in roots
        ):
            raise WorkflowError("App credentials must remain outside Worker paths")
    except (KeyError, TypeError, ValueError, OSError, WorkflowError):
        raise InvalidApplicationRequestError(
            "invalid PostgreSQL dispatch Worker configuration"
        ) from None
