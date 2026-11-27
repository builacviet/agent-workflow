"""Layer: Composition
Responsibility: Preflight trusted adapters and scope dependencies for a dispatch pass.
Dependencies: Application Dispatch, Domain errors, PostgreSQL, GitHub and Worker adapters.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from agent_workflow.app_config import AppConfig
from agent_workflow.application.dispatch.ports import CoderAssignment, DispatchClaim
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    DispatchAssignment,
    GetCoderAssignmentUseCase,
)
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.composition.worker_deps import compose_postgres_worker
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.github.app_token import ControllerAppToken
from agent_workflow.infrastructure.github.client import GitHubClient
from agent_workflow.infrastructure.runtime.codex_plugin import CodexCLIPlugin
from agent_workflow.runners.coder_runtime import validate_config
from agent_workflow.workers.trusted_git.app_auth import GitHubApp


@contextmanager
def compose_dispatch_execution(
    config: dict, app_config: AppConfig
) -> Iterator[RunDispatchPassUseCase]:
    """Preflight the trusted adapters and lend one factory to the use case."""
    _validate_worker_config(config)
    with PostgresFactory.open(app_config) as factory:
        yield create_dispatch_use_case(config, factory)


def create_dispatch_use_case(
    config: dict, factory: PostgresFactory
) -> RunDispatchPassUseCase:
    dependencies = compose_postgres_controller(factory)
    handoff_provider = dependencies.handoff_provider
    if (
        type(handoff_provider) is not PostgresHandoffProvider
        or handoff_provider.factory is not factory
    ):
        raise TypeError("Dispatch requires the composed PostgreSQL handoff provider")
    assignment = GetCoderAssignmentUseCase(
        context=create_get_review_context_use_case(factory)
    )
    runtime = None

    def execute_worker(
        claim: DispatchClaim,
        context: CoderAssignment,
        guard: Callable[[], None],
    ) -> str:
        nonlocal runtime
        guard()
        runtime = compose_postgres_worker(
            config, handoff_provider, dependencies, leadership_guard=guard
        )
        guard()
        return runtime.execute_assignment(claim, cast(DispatchAssignment, context))

    def finished(dispatch_id: str) -> None:
        if runtime is None:
            raise BusinessRuleError("missing dispatched Worker for terminal cleanup")
        runtime.release_engine(dispatch_id)

    return RunDispatchPassUseCase(
        leadership=dependencies.leadership,
        dispatch=dependencies.dispatch,
        assignment=assignment,
        execute_worker=execute_worker,
        on_finished=finished,
    )


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
            raise BusinessRuleError("unsupported Worker runtime selection")
        # Reuse the trusted Worker's credential checks without authenticating.
        worker = GitHubApp(
            config["app_id"], config["installation_id"], Path(config["pem"])
        )
        gate = config["controller_github_app"]
        controller = ControllerAppToken(
            app_id=gate["app_id"],
            installation_id=gate["installation_id"],
            pem_path=gate["pem"],
            request_json=GitHubClient().request_json,
        )
        controller_pem = Path(gate["pem"]).resolve(strict=True)
        if (
            not controller_pem.is_file()
            or worker.pem == controller_pem
            or worker.app_id == controller.app_id
            or worker.installation_id == controller.installation_id
        ):
            raise BusinessRuleError(
                "separate Controller and Worker credentials required"
            )
        roots = [Path(config["scratch"]).resolve()] + [
            Path(task["worktree"]).resolve() for task in config["tasks"].values()
        ]
        if any(
            pem.is_relative_to(root)
            for pem in (worker.pem, controller_pem)
            for root in roots
        ):
            raise BusinessRuleError("App credentials must remain outside Worker paths")
    except (KeyError, TypeError, ValueError, OSError):
        raise InvalidApplicationRequestError(
            "invalid PostgreSQL dispatch Worker configuration"
        ) from None
