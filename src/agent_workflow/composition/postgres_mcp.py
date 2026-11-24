"""Layer: Composition
Responsibility: Assemble scoped PostgreSQL dependencies for MCP workflow tools.
Dependencies: Application use cases and PostgreSQL adapters.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from time import time
from uuid import uuid4

from agent_workflow.app_config import AppConfig
from agent_workflow.application.review.use_cases.claim_review import ClaimReviewUseCase
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextUseCase,
)
from agent_workflow.application.review.use_cases.get_review_result import (
    GetReviewResultUseCase,
)
from agent_workflow.application.review.use_cases.list_review_queue import (
    ListReviewQueueUseCase,
)
from agent_workflow.application.review.use_cases.submit_review import (
    SubmitReviewUseCase,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    GetCoderAssignmentUseCase,
)
from agent_workflow.application.task.use_cases.register_task_handoff import (
    RegisterTaskHandoffUseCase,
)
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.infrastructure.database.readers.review_queue_reader import (
    PostgresReviewQueueScope,
)
from agent_workflow.infrastructure.database.repositories.review_writes import (
    PostgresReviewWriteScope,
)
from agent_workflow.infrastructure.database.repositories.task_handoff import (
    PostgresHandoffScope,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
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
            PostgresReviewQueueScope(PostgresWorkflowRepository(factory)),
            require_ci=app_config.ci_gate_required,
            clock=time,
        )
        yield queue, context, GetReviewResultUseCase(context)
    finally:
        factory.close()


@contextmanager
def postgres_mcp_task_operations(app_config: AppConfig):
    factory = PostgresFactory.open(app_config)
    try:
        repository = PostgresWorkflowRepository(factory)
        yield (
            RegisterTaskHandoffUseCase(PostgresHandoffScope(repository), clock=time),
            GetCoderAssignmentUseCase(create_get_review_context_use_case(factory)),
        )
    finally:
        factory.close()


@contextmanager
def postgres_mcp_review_writes(app_config: AppConfig):
    factory = PostgresFactory.open(app_config)
    try:
        scope = PostgresReviewWriteScope(PostgresWorkflowRepository(factory))
        kwargs = dict(
            clock=time,
            token=lambda: uuid4().hex,
            require_ci=app_config.ci_gate_required,
        )
        yield ClaimReviewUseCase(scope, **kwargs), SubmitReviewUseCase(scope, **kwargs)
    finally:
        factory.close()
