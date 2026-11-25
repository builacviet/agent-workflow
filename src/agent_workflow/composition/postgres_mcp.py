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
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.infrastructure.database.readers.review_queue_reader import (
    PostgresReviewQueueScope,
)
from agent_workflow.infrastructure.database.repositories.review_writes import (
    PostgresReviewWrites,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
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
def postgres_mcp_task_operations(
    app_config: AppConfig,
) -> Iterator[tuple[PostgresHandoffProvider, GetCoderAssignmentUseCase]]:
    factory = PostgresFactory.open(app_config)
    try:
        yield (
            PostgresHandoffProvider(factory),
            GetCoderAssignmentUseCase(create_get_review_context_use_case(factory)),
        )
    finally:
        factory.close()


@contextmanager
def postgres_mcp_review_writes(
    app_config: AppConfig,
) -> Iterator[tuple[ClaimReviewUseCase, SubmitReviewUseCase]]:
    with (
        PostgresFactory.open(app_config) as factory,
        factory.get_scoped_session() as session,
    ):
        uow = SqlAlchemyUnitOfWork(session)
        repository = PostgresReviewWrites(session)
        yield (
            ClaimReviewUseCase(
                uow,
                repository,
                clock=time,
                token=lambda: uuid4().hex,
                require_ci=app_config.ci_gate_required,
            ),
            SubmitReviewUseCase(
                uow,
                repository,
                clock=time,
                token=lambda: uuid4().hex,
                require_ci=app_config.ci_gate_required,
            ),
        )
