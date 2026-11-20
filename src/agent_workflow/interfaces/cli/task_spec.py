"""Transport schema for local PostgreSQL provisioning."""

from pydantic import ValidationError, ValidationInfo, field_validator

from agent_workflow.application.task.use_cases.authorize_task import (
    AuthorizeTaskCommand,
)
from agent_workflow.domain.contracts import TaskSpec as LegacyTaskSpec
from agent_workflow.domain.task.authorization import TaskAuthorizationSpec


class ProvisionTaskSpec(LegacyTaskSpec):
    """Preserve the legacy JSON contract at the new CLI boundary."""

    @field_validator("*", mode="before")
    @classmethod
    def validate_storage_text(cls, value: object, info: ValidationInfo) -> object:
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


_VALIDATION_REASONS = {
    "bool_type": "expected a boolean",
    "extra_forbidden": "unexpected field",
    "json_invalid": "malformed JSON",
    "list_type": "expected a list",
    "missing": "required field",
    "model_type": "expected an object",
    "string_too_short": "must not be empty",
    "string_type": "expected a string",
    "too_short": "must not be empty",
    "value_error": "contains unsupported text",
}


def format_task_spec_error(error: ValidationError) -> str:
    """Describe known fields and error types without rendering submitted data."""
    details = []
    for issue in error.errors(
        include_input=False, include_context=False, include_url=False
    )[:5]:
        field, *indices = issue["loc"] or ("",)
        path = "TaskSpec"
        if field in ProvisionTaskSpec.model_fields:
            path = field + "".join(
                f"[{index}]" for index in indices if type(index) is int
            )
        reason = _VALIDATION_REASONS.get(issue["type"], "invalid value")
        details.append(f"{path}: {reason}")
    if error.error_count() > len(details):
        details.append("additional validation errors omitted")
    return "; ".join(details)
