"""Dependency assembly for explicit local PostgreSQL task provisioning."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

from sqlalchemy.exc import SQLAlchemyError

from agent_workflow.app_config import AppConfig
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.application.task.ports import ITaskAuthorizationRepository
from agent_workflow.application.task.use_cases.authorize_task import (
    AuthorizeTaskUseCase,
)
from agent_workflow.infrastructure.database.repositories.task_authorization_repository import (
    SqlAlchemyTaskAuthorizationRepository,
)
from agent_workflow.infrastructure.database.session_factory import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


def create_authorize_task_use_case(factory: PostgresFactory) -> AuthorizeTaskUseCase:
    @contextmanager
    def open_scope() -> Iterator[tuple[IUnitOfWork, ITaskAuthorizationRepository]]:
        try:
            with SqlAlchemyUnitOfWork(factory) as unit:
                yield unit, SqlAlchemyTaskAuthorizationRepository(unit.session)
        except SQLAlchemyError:
            raise PostgresUnavailableError(
                "PostgreSQL task authorization failed"
            ) from None

    return AuthorizeTaskUseCase(open_scope)


@contextmanager
def postgres_task_authorization() -> Iterator[AuthorizeTaskUseCase]:
    dsn = os.environ.get("PG_RUNTIME_DSN")
    if dsn is None:
        raise ValueError("PG_RUNTIME_DSN is required")
    config = AppConfig(pg_runtime_dsn=dsn)
    if urlsplit(dsn).hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("PG_RUNTIME_DSN must target 127.0.0.1 or ::1")
    # libpq can get an alternate host address from its environment or service profile.
    if os.environ.get("PGHOSTADDR") or os.environ.get("PGSERVICE"):
        raise ValueError(
            "PGHOSTADDR and PGSERVICE are unsupported for local provisioning"
        )
    with PostgresFactory.open(config) as factory:
        yield create_authorize_task_use_case(factory)
