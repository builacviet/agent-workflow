"""Ports for review queries and queue lease recovery."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from agent_workflow.application.review.dtos import (
    ReviewContextSnapshot,
    ReviewQueueItem,
)


class IReviewContextReader(Protocol):
    def read(self, task_id: str) -> ReviewContextSnapshot | None:
        """Read one task and its ordered review history and events in one scope."""
        ...


class IReviewQueueReader(Protocol):
    def read(self, requested_effort: str | None) -> tuple[ReviewQueueItem, ...]:
        """Read eligible tasks from the current queue scope."""
        ...


ReviewQueueOperations = tuple[Callable[[], None], IReviewQueueReader]


class IReviewQueueScope(Protocol):
    def __call__(self) -> AbstractContextManager[ReviewQueueOperations]:
        """Provide lease recovery and a Reader in one transaction."""
        ...
