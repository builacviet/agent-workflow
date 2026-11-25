"""Layer: Infrastructure
Responsibility: Read a review context snapshot in a single read-only scope.
Dependencies: Application review DTOs and scoped PostgreSQL repositories.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager

from agent_workflow.application.review.dtos import ReviewContextSnapshot
from agent_workflow.infrastructure.database.repositories.review_repository import (
    SqlAlchemyReviewRepository,
)
from agent_workflow.infrastructure.database.repositories.task_repository import (
    SqlAlchemyTaskRepository,
)

ReviewRepositories = tuple[SqlAlchemyTaskRepository, SqlAlchemyReviewRepository]


class SqlAlchemyReviewContextReader:
    def __init__(
        self,
        open_repositories: Callable[[], AbstractContextManager[ReviewRepositories]],
    ) -> None:
        self._open_repositories = open_repositories

    def read(self, task_id: str) -> ReviewContextSnapshot | None:
        with self._open_repositories() as (tasks, reviews):
            task = tasks.get_task(task_id)
            return (
                ReviewContextSnapshot(
                    task=task,
                    previous_reviews=reviews.list_reviews(task_id),
                    events=reviews.list_events(task_id),
                )
                if task is not None
                else None
            )
