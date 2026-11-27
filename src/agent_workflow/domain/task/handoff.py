"""Layer: Domain
Responsibility: Validate handoff identity and workflow transitions.
Dependencies: Canonical workflow identity and state definitions.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import REPOSITORY, validate_sha


class TaskEventAction(StrEnum):
    HANDOFF = "HANDOFF"
    CI_REPAIR_HANDOFF = "CI_REPAIR_HANDOFF"


@dataclass(frozen=True)
class HandoffRequest:
    task_id: str
    repository: str
    branch: str
    base_sha: str
    new_head_sha: str
    handoff_file_path: str
    test_summary: str

    def validate(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise BusinessRuleError("unknown authorized task")
        if not isinstance(self.repository, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository
        ):
            raise BusinessRuleError("repository must be an owner/name")
        if (
            not isinstance(self.branch, str)
            or not self.branch.strip()
            or any(char.isspace() or ord(char) < 32 for char in self.branch)
        ):
            raise BusinessRuleError("branch must be a nonblank ref")
        validate_sha(self.base_sha)
        validate_sha(self.new_head_sha)
        path = self.handoff_file_path
        if (
            not isinstance(path, str)
            or not path.strip()
            or path.startswith(("/", "\\"))
            or ".." in path.replace("\\", "/").split("/")
            or ":" in path
            or "\0" in path
        ):
            raise BusinessRuleError("handoff path must be repository-relative")
        if not isinstance(self.test_summary, str) or not self.test_summary.strip():
            raise BusinessRuleError("test summary required")


@dataclass(frozen=True)
class HandoffTask:
    task_id: str
    repository: str
    branch: str
    base_sha: str
    head_sha: str | None
    status: WorkflowStatus
    completed_review_rounds: int
    context_paths: tuple[str, ...]


def validate_handoff(task: HandoffTask, request: HandoffRequest) -> None:
    if task.repository != REPOSITORY or (
        request.repository,
        request.branch,
        request.base_sha,
    ) != (task.repository, task.branch, task.base_sha):
        raise BusinessRuleError("handoff differs from authorized task")
    if request.handoff_file_path not in task.context_paths:
        raise BusinessRuleError("handoff path is not authorized")
    if (
        task.status
        not in {
            WorkflowStatus.AWAITING_HANDOFF,
            WorkflowStatus.CHANGES_REQUESTED,
            WorkflowStatus.READY_IN_REVIEW,
        }
        or task.completed_review_rounds >= 2
    ):
        raise BusinessRuleError("handoff is not permitted in this state")
    if (
        task.status == WorkflowStatus.CHANGES_REQUESTED
        and request.new_head_sha == task.head_sha
    ):
        raise BusinessRuleError("correction requires a new pushed SHA")


def validate_repair(
    task: HandoffTask, request: HandoffRequest, prior_path: str | None, seen: bool
) -> bool:
    if prior_path is not None and prior_path != request.handoff_file_path:
        raise BusinessRuleError("handoff path differs from current handoff")
    if request.new_head_sha == task.head_sha:
        return False
    if (
        prior_path is None
        or task.head_sha is None
        or request.new_head_sha == request.base_sha
    ):
        raise BusinessRuleError("CI repair requires a new SHA and current handoff")
    if seen:
        raise BusinessRuleError("stale CI repair SHA")
    return True
