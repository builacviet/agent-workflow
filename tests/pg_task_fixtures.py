"""Narrow setup helpers for disposable PostgreSQL tests; use current use cases."""

from agent_workflow.composition.task_deps import create_authorize_task_use_case
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.domain.task.handoff import HandoffRequest
from agent_workflow.interfaces.cli.task_spec import ProvisionTaskSpec


def authorize_task(factory, data):
    command = ProvisionTaskSpec.model_validate(data).to_command()
    with create_authorize_task_use_case(factory) as operation:
        operation.execute(command)


def record_handoff(factory, task_id, repo, branch, base, head, path, summary):
    with PostgresHandoffProvider(factory, clock=lambda: 1000.0)() as operation:
        return operation.execute(
            HandoffRequest(task_id, repo, branch, base, head, path, summary)
        )
