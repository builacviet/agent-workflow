"""Explicit one-shot PostgreSQL dispatch with caller-owned state and leadership."""

from collections.abc import Callable
from pathlib import Path

from agent_workflow.app_config import AppConfig, validate_pg_runtime_dsn
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.postgres_leader_runner import run_controller_once
from agent_workflow.composition.worker_deps import compose_postgres_worker
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.integrations.github_app_checks import ControllerAppToken
from agent_workflow.runners.coder_runtime import validate_config
from agent_workflow.runners.runtime_plugins import CodexCLIPlugin
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.workers.trusted_git.app_auth import GitHubApp


def _validate(config: dict, app_config: AppConfig) -> None:
    """Validate local settings only: no database, Docker or GitHub requests."""
    validate_pg_runtime_dsn(app_config.pg_runtime_dsn)
    try:
        tasks = config.get("tasks")
        if (
            not isinstance(tasks, dict)
            or not tasks
            or any(
                not isinstance(key, str)
                or not key.strip()
                or not isinstance(task, dict)
                for key, task in tasks.items()
            )
        ):
            raise WorkflowError("explicit task mapping required")
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
            Path(task["worktree"]).resolve() for task in tasks.values()
        ]
        if any(
            pem.is_relative_to(root)
            for pem in (worker.pem, controller_pem)
            for root in roots
        ):
            raise WorkflowError("App credentials must remain outside Worker paths")
    except (KeyError, TypeError, ValueError, OSError, WorkflowError):
        raise WorkflowError(
            "invalid PostgreSQL dispatch Worker configuration"
        ) from None


def run_postgres_dispatch_once(config: dict, app_config: AppConfig) -> bool:
    """Own exactly one factory; borrow its adapters only after trusted validation."""
    _validate(config, app_config)
    with PostgresFactory.open(app_config) as factory:
        dependencies = compose_postgres_controller(factory).controller
        service = WorkflowService(
            repository=dependencies.workflow, require_ci=app_config.ci_gate_required
        )
        runtime = None

        def execute(claim: dict, context: dict, guard: Callable[[], None]) -> str:
            nonlocal runtime
            guard()
            if (
                claim["task_id"] not in config["tasks"]
                or context.get("task_id") != claim["task_id"]
            ):
                raise WorkflowError("dispatch outside exact task allowlist")
            runtime = compose_postgres_worker(
                config, service, dependencies, leadership_guard=guard
            )
            guard()
            return runtime.execute(claim, context)

        def finished(dispatch_id: str) -> None:
            if runtime is None:
                raise WorkflowError("missing dispatched Worker for terminal cleanup")
            runtime.release_engine(dispatch_id)

        return run_controller_once(
            dependencies,
            service,
            set(config["tasks"]),
            execute,
            config.get("timeout_seconds", 1800),
            on_finished=finished,
        )
