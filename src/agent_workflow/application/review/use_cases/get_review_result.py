"""Layer: Application
Responsibility: Derive review result fields from one review context snapshot.
Dependencies: The review context use case and Domain workflow statuses.
"""

from dataclasses import dataclass

from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
    GetReviewContextOutput,
    GetReviewContextUseCase,
)
from agent_workflow.domain.states import WorkflowStatus


@dataclass(frozen=True)
class GetReviewResultOutput:
    context: GetReviewContextOutput
    actual_effort: str | None
    escalation_reason: str | None


class GetReviewResultUseCase:
    def __init__(self, context: GetReviewContextUseCase) -> None:
        self._context = context

    def execute(self, request: GetReviewContextInput) -> GetReviewResultOutput:
        context = self._context.execute(request)
        latest = context.previous_reviews[-1] if context.previous_reviews else None
        return GetReviewResultOutput(
            context=context,
            actual_effort=latest.actual_effort if latest else None,
            escalation_reason=(
                latest.summary
                if latest and context.task.status == WorkflowStatus.ESCALATED
                else None
            ),
        )
