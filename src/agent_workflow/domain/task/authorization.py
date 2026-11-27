"""Task specification and invariants for local operator authorization."""

from dataclasses import dataclass
from typing import ClassVar

from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import (
    REPOSITORY,
    validate_sha,
)

_LOCAL_EFFORTS = frozenset(("instant", "medium", "high"))


@dataclass(frozen=True)
class TaskAuthorizationSpec:
    task_id: str
    title: str
    description: str
    acceptance_criteria: tuple[str, ...]
    definition_of_done: str
    repository: str
    branch: str
    base_sha: str
    requested_effort: str
    risk_level: str
    context_paths: tuple[str, ...]
    require_verified_effort: bool = True
    synthetic: bool = False

    def __post_init__(self) -> None:
        for name in (
            "task_id",
            "title",
            "description",
            "definition_of_done",
            "repository",
            "branch",
            "base_sha",
            "requested_effort",
            "risk_level",
        ):
            if type(getattr(self, name)) is not str:
                raise BusinessRuleError(f"{name} must be a string")
        for name in ("task_id", "title", "description", "definition_of_done", "branch"):
            if not getattr(self, name):
                raise BusinessRuleError(f"{name} must be nonempty")
        for name in ("acceptance_criteria", "context_paths"):
            values = getattr(self, name)
            if (
                type(values) is not tuple
                or not values
                or any(type(value) is not str for value in values)
            ):
                raise BusinessRuleError(f"{name} must be a nonempty tuple of strings")
        if (
            type(self.require_verified_effort) is not bool
            or type(self.synthetic) is not bool
        ):
            raise BusinessRuleError(
                "require_verified_effort and synthetic must be booleans"
            )
        if self.repository != REPOSITORY or self.requested_effort not in _LOCAL_EFFORTS:
            raise BusinessRuleError("unauthorized repository or unsupported effort")
        validate_sha(self.base_sha)
        if any(
            not path
            or path.startswith(("/", "\\"))
            or ".." in path.replace("\\", "/").split("/")
            or ":" in path
            for path in self.context_paths
        ):
            raise BusinessRuleError("context paths must be repository-relative")

    def match_existing(self, existing: "TaskAuthorizationSpec") -> None:
        if self != existing:
            raise BusinessRuleError("task authorization conflicts with stored metadata")


@dataclass(frozen=True)
class TaskAuthorization:
    """A new Task and its corresponding initial authorization event."""

    spec: TaskAuthorizationSpec
    created_at: float
    authorized_at: float
    status: ClassVar[WorkflowStatus] = WorkflowStatus.AWAITING_HANDOFF
