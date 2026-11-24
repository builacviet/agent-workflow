"""Layer: Application
Responsibility: Build coder assignment from the canonical review context.
Dependencies: Review context use case and immutable review DTOs.
"""

from dataclasses import dataclass
from typing import Protocol

from agent_workflow.application.dispatch.ports import CoderAssignment
from agent_workflow.application.review.dtos import ReviewFinding
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
    GetReviewContextOutput,
)


class IAssignmentContextReader(Protocol):
    def execute(self, request: GetReviewContextInput) -> GetReviewContextOutput: ...


@dataclass(frozen=True)
class CoderAssignmentOutput:
    context: GetReviewContextOutput
    unresolved_findings: tuple[tuple[ReviewFinding, str], ...]


class DispatchAssignment(CoderAssignment):
    """Dispatch facts plus the immutable assignment for the Worker boundary."""

    details: CoderAssignmentOutput


class GetCoderAssignmentUseCase:
    def __init__(self, context: IAssignmentContextReader):
        self.context = context

    def execute(self, task_id: str) -> CoderAssignmentOutput:
        context = self.context.execute(GetReviewContextInput(task_id))
        resolutions = {
            key: value
            for review in context.previous_reviews
            for key, value in review.prior_resolutions
        }
        return CoderAssignmentOutput(
            context,
            tuple(
                (finding, resolutions.get(finding.finding_id, "NOT_VERIFIED"))
                for review in context.previous_reviews
                for finding in review.findings
                if resolutions.get(finding.finding_id) != "FIXED"
            ),
        )

    def get_coder_assignment(self, task_id: str) -> DispatchAssignment:
        details = self.execute(task_id)
        task = details.context.task
        return DispatchAssignment(
            task_id=task.task_id,
            completed_review_rounds=task.completed_review_rounds,
            status=task.status,
            current_head_sha=task.current_head_sha,
            base_sha=task.base_sha,
            details=details,
        )
