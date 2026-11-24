"""Layer: Composition
Responsibility: Assemble a task handoff use case on a fresh scoped Session.
Dependencies: Application use case and canonical PostgreSQL adapters.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import time

from agent_workflow.application.task.use_cases.register_task_handoff import (
    RegisterTaskHandoffUseCase,
)
from agent_workflow.infrastructure.database.repositories.task_handoff import (
    PostgresHandoffRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True)
class PostgresHandoffProvider:
    factory: PostgresFactory
    clock: Callable[[], float] = time

    @contextmanager
    def __call__(self) -> Iterator[RegisterTaskHandoffUseCase]:
        with self.factory.get_scoped_session() as session:
            yield RegisterTaskHandoffUseCase(
                SqlAlchemyUnitOfWork(session),
                PostgresHandoffRepository(session),
                clock=self.clock,
            )
