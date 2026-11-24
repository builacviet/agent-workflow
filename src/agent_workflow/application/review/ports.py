"""Layer: Application
Responsibility: Define the review read, lease persistence and transaction ports.
Dependencies: Immutable Application review DTOs.
"""

from contextlib import AbstractContextManager
from typing import Protocol

from agent_workflow.application.review.dtos import (
    ActiveReviewClaim,
    ClaimedReviewTask,
    ReviewContextSnapshot,
    ReviewLeaseTransition,
    ReviewQueueItem,
)


class IReviewContextReader(Protocol):
    def read(self, task_id: str) -> ReviewContextSnapshot | None:
        """Read one task and its ordered review history and events in one scope."""
        ...


class IReviewQueueReader(Protocol):
    def read(self, requested_effort: str | None) -> tuple[ReviewQueueItem, ...]:
        """Read ready tasks in task ID order, optionally filtered by effort."""
        ...

    def latest_head_handoff_action(
        self, task_id: str, head_sha: str | None
    ) -> str | None: ...

    def has_successful_ci(self, task_id: str, head_sha: str | None) -> bool: ...


class IReviewLeaseStore(Protocol):
    def lock_claimed_tasks(self) -> tuple[ClaimedReviewTask, ...]:
        """Lock claimed tasks in task ID order for this transaction."""
        ...

    def find_active_claim(self, task_id: str) -> ActiveReviewClaim | None: ...

    def expire_claim(self, token: str) -> None: ...

    def record_transition(self, transition: ReviewLeaseTransition) -> None:
        """Persist the event and task status in the current transaction."""
        ...


ReviewQueueOperations = tuple[IReviewLeaseStore, IReviewQueueReader]


class IReviewQueueScope(Protocol):
    def __call__(self) -> AbstractContextManager[ReviewQueueOperations]:
        """Provide lease persistence and a Reader in one transaction."""
        ...
