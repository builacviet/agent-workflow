"""Transport schema for local PostgreSQL provisioning."""

from agent_workflow.application.task.use_cases.authorize_task import (
    AuthorizeTaskCommand,
    validate_authorization_text,
)
from agent_workflow.domain.contracts import TaskSpec as LegacyTaskSpec
from agent_workflow.domain.task.authorization import TaskAuthorizationSpec


class ProvisionTaskSpec(LegacyTaskSpec):
    """Preserve the legacy JSON contract at the new CLI boundary."""

    def to_command(self) -> AuthorizeTaskCommand:
        values = self.model_dump()
        values["acceptance_criteria"] = tuple(self.acceptance_criteria)
        values["context_paths"] = tuple(self.context_paths)
        spec = TaskAuthorizationSpec(**values)
        validate_authorization_text(spec)
        return AuthorizeTaskCommand(spec)
