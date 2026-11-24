"""Scoped PostgreSQL dependencies for the workflow MCP tools."""

from collections.abc import Iterator
from contextlib import contextmanager

from agent_workflow.app_config import AppConfig
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextUseCase,
)
from agent_workflow.application.review.use_cases.get_review_result import (
    GetReviewResultUseCase,
)
from agent_workflow.application.review.use_cases.list_review_queue import (
    ListReviewQueueUseCase,
)
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.domain.service import WorkflowService
from agent_workflow.infrastructure.database.readers.review_queue_reader import (
    PostgresReviewQueueScope,
)
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


@contextmanager
def postgres_mcp_review_reads(
    app_config: AppConfig,
) -> Iterator[
    tuple[ListReviewQueueUseCase, GetReviewContextUseCase, GetReviewResultUseCase]
]:
    factory = PostgresFactory.open(app_config)
    try:
        context = create_get_review_context_use_case(factory)
        queue = ListReviewQueueUseCase(
            PostgresReviewQueueScope(
                PostgresWorkflowRepository(factory),
                require_ci=app_config.ci_gate_required,
            )
        )
        yield queue, context, GetReviewResultUseCase(context)
    finally:
        factory.close()


@contextmanager
def postgres_mcp_service(app_config: AppConfig) -> Iterator[WorkflowService]:
    """Own the factory for the lifetime of the yielded service."""
    factory = PostgresFactory.open(app_config)
    try:
        yield WorkflowService(
            repository=PostgresWorkflowRepository(factory),
            require_ci=app_config.ci_gate_required,
        )
    finally:
        factory.close()
