"""Dependency assembly for the review use case."""

from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextUseCase,
)
from agent_workflow.infrastructure.database.repositories.sqlalchemy_review_context_reader import (
    SqlAlchemyReviewContextReader,
)
from agent_workflow.storage.postgres_connection import PostgresFactory


def create_get_review_context_use_case(
    factory: PostgresFactory,
) -> GetReviewContextUseCase:
    """Borrow the caller's factory for per-execution read scopes."""
    return GetReviewContextUseCase(reader=SqlAlchemyReviewContextReader(factory))
