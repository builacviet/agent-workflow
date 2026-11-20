"""Dependency assembly for explicit local PostgreSQL task provisioning."""

from collections.abc import Iterator
from contextlib import contextmanager

from agent_workflow.app_config import AppConfig
from agent_workflow.application.task.use_cases.authorize_task import (
    AuthorizeTaskUseCase,
)
from agent_workflow.infrastructure.database.repositories.task_authorization_repository import (
    SqlAlchemyTaskAuthorizationRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


@contextmanager
def create_authorize_task_use_case(
    factory: PostgresFactory,
) -> Iterator[AuthorizeTaskUseCase]:
    with factory.scoped_session() as session:
        unit = SqlAlchemyUnitOfWork(session)
        repository = SqlAlchemyTaskAuthorizationRepository(session)
        yield AuthorizeTaskUseCase(unit, repository)


@contextmanager
def task_authorization_scope() -> Iterator[AuthorizeTaskUseCase]:
    config = AppConfig.for_local_task_authorization()
    with (
        PostgresFactory.open(config) as factory,
        create_authorize_task_use_case(factory) as use_case,
    ):
        yield use_case
