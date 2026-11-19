"""Read persisted review context for an authorized task."""

from dataclasses import dataclass

from agent_workflow.application.review.dtos import (
    PreviousReview,
    ReviewEvent,
    ReviewTask,
)
from agent_workflow.application.review.ports import IReviewContextReader
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError


@dataclass(frozen=True)
class GetReviewContextInput:
    task_id: str

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise WorkflowError("task_id must not be blank")


@dataclass(frozen=True)
class GetReviewContextOutput:
    """Business data for review. The transport owns GitHub URL formatting."""

    task: ReviewTask
    review_round: int
    rounds_remaining: int
    previous_reviewed_sha: str | None
    previous_verdict: WorkflowStatus | None
    previous_reviews: tuple[PreviousReview, ...]
    events: tuple[ReviewEvent, ...]


class GetReviewContextUseCase:
    def __init__(self, reader: IReviewContextReader) -> None:
        self._reader = reader

    def execute(self, request: GetReviewContextInput) -> GetReviewContextOutput:
        snapshot = self._reader.read(request.task_id)
        if snapshot is None:
            raise WorkflowError("unknown authorized task")

        task = snapshot.task
        previous = snapshot.previous_reviews[-1] if snapshot.previous_reviews else None
        return GetReviewContextOutput(
            task=task,
            review_round=min(task.completed_review_rounds + 1, task.max_review_rounds),
            rounds_remaining=task.max_review_rounds - task.completed_review_rounds,
            previous_reviewed_sha=previous.reviewed_head_sha if previous else None,
            previous_verdict=previous.verdict if previous else None,
            previous_reviews=snapshot.previous_reviews,
            events=snapshot.events,
        )
