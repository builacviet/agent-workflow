"""Narrow setup helpers for disposable PostgreSQL tests; use current use cases."""

from agent_workflow.application.task.use_cases.register_task_handoff import (
    RegisterTaskHandoffUseCase,
)
from agent_workflow.composition.task_deps import create_authorize_task_use_case
from agent_workflow.domain.task.handoff import HandoffRequest
from agent_workflow.infrastructure.database.repositories.task_handoff import (
    PostgresHandoffScope,
)
from agent_workflow.interfaces.cli.task_spec import ProvisionTaskSpec


def authorize_task(factory, data):
    command = ProvisionTaskSpec.model_validate(data).to_command()
    with create_authorize_task_use_case(factory) as operation:
        operation.execute(command)


def record_handoff(repository, task_id, repo, branch, base, head, path, summary):
    return RegisterTaskHandoffUseCase(
        PostgresHandoffScope(repository), clock=lambda: 1000.0
    ).execute(HandoffRequest(task_id, repo, branch, base, head, path, summary))
