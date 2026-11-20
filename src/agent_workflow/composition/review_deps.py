"""Dependency assembly for the review use case."""

from collections.abc import Iterator
from contextlib import contextmanager

from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextUseCase,
)
from agent_workflow.infrastructure.database.repositories.review_context_reader import (
    ReviewRepositories,
    SqlAlchemyReviewContextReader,
)
from agent_workflow.infrastructure.database.repositories.review_repository import (
    SqlAlchemyReviewRepository,
)
from agent_workflow.infrastructure.database.repositories.task_repository import (
    SqlAlchemyTaskRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


def create_get_review_context_use_case(
    factory: PostgresFactory,
) -> GetReviewContextUseCase:
    """Borrow the caller's factory for per-execution read scopes."""

    @contextmanager
    def open_repositories() -> Iterator[ReviewRepositories]:
        with (
            factory.scoped_session() as session,
            SqlAlchemyUnitOfWork(session, read_only=True),
        ):
            yield SqlAlchemyTaskRepository(session), SqlAlchemyReviewRepository(session)

    return GetReviewContextUseCase(
        reader=SqlAlchemyReviewContextReader(open_repositories)
    )
