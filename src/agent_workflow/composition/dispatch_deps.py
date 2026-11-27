"""Layer: Composition
Responsibility: Preflight trusted adapters and scope dependencies for a dispatch pass.
Dependencies: Application Dispatch and runtime services, PostgreSQL and Worker adapters.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial

from agent_workflow.app_config import AppConfig
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.application.runtime.services.agent_execution import (
    AgentExecutionService,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    GetCoderAssignmentUseCase,
)
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.composition.worker_deps import compose_postgres_worker
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.runtime.codex_plugin import CodexCLIPlugin
from agent_workflow.infrastructure.runtime.registry import (
    RuntimeRegistry,
    runtime_spec_from_config,
)
from agent_workflow.infrastructure.runtime.worker_config import (
    validate_dispatch_worker_config,
)
from agent_workflow.infrastructure.runtime.worker_payload import (
    worker_assignment_payload,
)
from agent_workflow.sandbox_state_paths import validate_sandbox_state_dir
from agent_workflow.workers.trusted_git.app_auth import GitHubApp


@contextmanager
def compose_dispatch_execution(
    config: dict, app_config: AppConfig
) -> Iterator[RunDispatchPassUseCase]:
    """Preflight the trusted adapters and lend one factory to the use case."""
    if type(app_config) is not AppConfig:
        raise TypeError("Dispatch requires explicit AppConfig")
    validate_sandbox_state_dir(app_config.sandbox_state_dir)
    registry = RuntimeRegistry((CodexCLIPlugin(),))
    validate_dispatch_worker_config(config, registry, GitHubApp)
    with PostgresFactory.open(app_config) as factory:
        yield create_dispatch_use_case(
            config, factory, registry=registry, app_config=app_config
        )


def create_dispatch_use_case(
    config: dict,
    factory: PostgresFactory,
    *,
    registry: RuntimeRegistry,
    app_config: AppConfig,
) -> RunDispatchPassUseCase:
    if type(registry) is not RuntimeRegistry:
        raise TypeError("Dispatch requires the composed runtime registry")
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
    agent_execution = AgentExecutionService(
        adapter_factory=partial(
            compose_postgres_worker,
            config,
            handoff_provider,
            dependencies,
            app_config=app_config,
        ),
        runtime_spec=partial(runtime_spec_from_config, config),
        registry=registry,
        assignment_payload=worker_assignment_payload,
        dispatch=dependencies.dispatch,
        handoff_provider=handoff_provider,
        ci=dependencies.ci_tracking,
        require_ci_tracking=True,
    )

    return RunDispatchPassUseCase(
        leadership=dependencies.leadership,
        dispatch=dependencies.dispatch,
        assignment=assignment,
        agent_execution=agent_execution,
    )
