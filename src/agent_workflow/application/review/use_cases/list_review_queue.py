"""List reviewable tasks after recovering expired claims."""

from agent_workflow.application.review.dtos import ReviewQueueItem
from agent_workflow.application.review.ports import IReviewQueueScope
from agent_workflow.domain.workflow_identity import WorkflowError


class ListReviewQueueUseCase:
    def __init__(self, scope: IReviewQueueScope) -> None:
        self._scope = scope

    def execute(
        self, requested_effort: str | None = None
    ) -> tuple[ReviewQueueItem, ...]:
        if requested_effort is not None and not isinstance(requested_effort, str):
            raise WorkflowError("requested_effort must be a string")
        with self._scope() as (recover_expired_claims, reader):
            recover_expired_claims()
            return reader.read(requested_effort)
