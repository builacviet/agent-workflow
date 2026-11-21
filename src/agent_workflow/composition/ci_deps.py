"""Feature-local dependency assembly for Controller CI persistence."""

from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SessionScope,
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.storage.postgres_ci_state import PostgresControllerCiState
from agent_workflow.storage.postgres_connection import PostgresFactory


def create_ci_state(factory: PostgresFactory) -> PostgresControllerCiState:
    """Assemble CI reader/repository without opening a Session.

    Resolve the session provider on each invocation so operator scopes and
    post-composition test replacements preserve the existing late binding.
    """

    def session_scope() -> AbstractContextManager[Session]:
        return factory.get_scoped_session()

    provider: SessionScope = session_scope
    return PostgresControllerCiState(
        reader=SqlAlchemyCiStateReader(provider),
        repository=SqlAlchemyCiStateRepository(provider),
        factory=factory,
    )
