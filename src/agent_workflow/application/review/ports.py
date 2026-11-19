"""Read port for the review context use case."""

from typing import Protocol

from agent_workflow.application.review.dtos import ReviewContextSnapshot


class IReviewContextReader(Protocol):
    def read(self, task_id: str) -> ReviewContextSnapshot | None:
        """Read one task and its ordered review history and events in one scope."""
        ...
