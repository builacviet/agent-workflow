"""Layer: Domain
Responsibility: Validate handoff identity and workflow transitions.
Dependencies: Canonical workflow identity and state definitions.
"""

from dataclasses import dataclass
from enum import StrEnum

from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import (
    WorkflowError,
    canonical_repository,
    validate_sha,
)


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
            raise WorkflowError("unknown authorized task")
        validate_sha(self.new_head_sha)
        if not isinstance(self.test_summary, str) or not self.test_summary.strip():
            raise WorkflowError("test summary required")


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
    if (canonical_repository(request.repository), request.branch, request.base_sha) != (
        canonical_repository(task.repository),
        task.branch,
        task.base_sha,
    ):
        raise WorkflowError("handoff differs from authorized task")
    if request.handoff_file_path not in task.context_paths:
        raise WorkflowError("handoff path is not authorized")
    if (
        task.status
        not in {
            WorkflowStatus.AWAITING_HANDOFF,
            WorkflowStatus.CHANGES_REQUESTED,
            WorkflowStatus.READY_IN_REVIEW,
        }
        or task.completed_review_rounds >= 2
    ):
        raise WorkflowError("handoff is not permitted in this state")
    if (
        task.status == WorkflowStatus.CHANGES_REQUESTED
        and request.new_head_sha == task.head_sha
    ):
        raise WorkflowError("correction requires a new pushed SHA")


def validate_repair(
    task: HandoffTask, request: HandoffRequest, prior_path: str | None, seen: bool
) -> bool:
    if prior_path is not None and prior_path != request.handoff_file_path:
        raise WorkflowError("handoff path differs from current handoff")
    if request.new_head_sha == task.head_sha:
        return False
    if (
        prior_path is None
        or task.head_sha is None
        or request.new_head_sha == request.base_sha
    ):
        raise WorkflowError("CI repair requires a new SHA and current handoff")
    if seen:
        raise WorkflowError("stale CI repair SHA")
    return True
