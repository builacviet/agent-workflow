"""Layer: Application
Responsibility: Recover expired review leases and select CI-eligible queue items.
Dependencies: Review DTOs, persistence ports, unit of work and Domain workflow policy.
"""

from collections.abc import Callable

from agent_workflow.application.review.dtos import (
    ReviewLeaseTransition,
    ReviewQueueItem,
)
from agent_workflow.application.review.ports import (
    IReviewLeaseStore,
    IReviewQueueReader,
)
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.budget import check_deadline
from agent_workflow.domain.review.events import ReviewEventAction
from agent_workflow.domain.review.policy import require_ci
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError


class ListReviewQueueUseCase:
    def __init__(
        self,
        uow: IUnitOfWork,
        leases: IReviewLeaseStore,
        reader: IReviewQueueReader,
        *,
        require_ci: bool,
        clock: Callable[[], float],
    ) -> None:
        self._uow = uow
        self._leases = leases
        self._reader = reader
        self._require_ci = require_ci
        self._clock = clock

    def execute(
        self, requested_effort: str | None = None
    ) -> tuple[ReviewQueueItem, ...]:
        if requested_effort is not None and not isinstance(requested_effort, str):
            raise WorkflowError("requested_effort must be a string")
        with self._uow:
            recovered = self._recover_expired_claims()
            items = tuple(
                item
                for item in self._reader.read(requested_effort)
                if self._ci_allows(item)
            )
            if recovered:
                self._uow.commit()
            else:
                check_deadline()
            return items

    def _ci_allows(self, item: ReviewQueueItem) -> bool:
        handoff = (
            self._reader.latest_head_handoff_action(item.task_id, item.head_sha)
            if not self._require_ci
            else None
        )
        if require_ci(self._require_ci, handoff, False):
            return True
        return require_ci(
            self._require_ci,
            handoff,
            self._reader.has_successful_ci(item.task_id, item.head_sha),
        )

    def _recover_expired_claims(self) -> bool:
        leases = self._leases
        recovered = False
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
                recovered = True
        return recovered
