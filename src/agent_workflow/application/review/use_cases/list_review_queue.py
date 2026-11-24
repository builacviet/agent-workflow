"""Layer: Application
Responsibility: Recover expired review leases and select CI-eligible queue items.
Dependencies: Review DTOs, persistence ports and Domain workflow types.
"""

from collections.abc import Callable

from agent_workflow.application.review.dtos import (
    ReviewLeaseTransition,
    ReviewQueueItem,
)
from agent_workflow.application.review.ports import (
    IReviewLeaseStore,
    IReviewQueueReader,
    IReviewQueueScope,
)
from agent_workflow.domain.review.events import ReviewEventAction
from agent_workflow.domain.review.policy import require_ci
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError


class ListReviewQueueUseCase:
    def __init__(
        self,
        scope: IReviewQueueScope,
        *,
        require_ci: bool,
        clock: Callable[[], float],
    ) -> None:
        self._scope = scope
        self._require_ci = require_ci
        self._clock = clock

    def execute(
        self, requested_effort: str | None = None
    ) -> tuple[ReviewQueueItem, ...]:
        if requested_effort is not None and not isinstance(requested_effort, str):
            raise WorkflowError("requested_effort must be a string")
        with self._scope() as (leases, reader):
            self._recover_expired_claims(leases)
            return tuple(
                item
                for item in reader.read(requested_effort)
                if self._ci_allows(reader, item)
            )

    def _ci_allows(self, reader: IReviewQueueReader, item: ReviewQueueItem) -> bool:
        handoff = (
            reader.latest_head_handoff_action(item.task_id, item.head_sha)
            if not self._require_ci
            else None
        )
        if require_ci(self._require_ci, handoff, False):
            return True
        return require_ci(
            self._require_ci,
            handoff,
            reader.has_successful_ci(item.task_id, item.head_sha),
        )

    def _recover_expired_claims(self, leases: IReviewLeaseStore) -> None:
        for task in leases.lock_claimed_tasks():
            claim = leases.find_active_claim(task.task_id)
            if claim and claim.expires_at <= self._clock():
                leases.expire_claim(claim.token)
                leases.record_transition(
                    ReviewLeaseTransition(
                        task=task,
                        next_status=WorkflowStatus.READY_IN_REVIEW,
                        action=ReviewEventAction.LEASE_EXPIRED,
                        timestamp=self._clock(),
                    )
                )
