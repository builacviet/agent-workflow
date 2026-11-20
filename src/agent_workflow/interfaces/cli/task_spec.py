"""Transport schema for local PostgreSQL provisioning."""

from pydantic import ValidationInfo, field_validator

from agent_workflow.application.task.use_cases.authorize_task import (
    AuthorizeTaskCommand,
)
from agent_workflow.domain.contracts import TaskSpec as LegacyTaskSpec
from agent_workflow.domain.task.authorization import TaskAuthorizationSpec


class ProvisionTaskSpec(LegacyTaskSpec):
    """Preserve the legacy JSON contract at the new CLI boundary."""

    @field_validator("*")
    @classmethod
    def validate_postgres_text(cls, value: object, info: ValidationInfo) -> object:
        """Reject NUL and non-UTF-8 text at the inbound CLI boundary."""
        values = (
            (value,)
            if isinstance(value, str)
            else value
            if isinstance(value, list)
            else ()
        )
        for item in values:
            if not isinstance(item, str):
                continue
            if "\0" in item:
                raise ValueError(f"{info.field_name} must not contain NUL")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError(f"{info.field_name} must be valid UTF-8") from None
        return value

    def to_command(self) -> AuthorizeTaskCommand:
        values = self.model_dump()
        values["acceptance_criteria"] = tuple(self.acceptance_criteria)
        values["context_paths"] = tuple(self.context_paths)
        spec = TaskAuthorizationSpec(**values)
        return AuthorizeTaskCommand(spec)
