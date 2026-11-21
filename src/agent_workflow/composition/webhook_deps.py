"""Dependency assembly for the PostgreSQL webhook use case."""

from agent_workflow.application.webhook.receive_webhook import ReceiveWebhookUseCase
from agent_workflow.infrastructure.database.repositories.webhook_inbox_repository import (
    SqlAlchemyWebhookInboxRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory


def create_webhook_use_case(
    factory: PostgresFactory,
) -> ReceiveWebhookUseCase:
    """Assemble the validated webhook use case and its durable inbox port."""
    repository = SqlAlchemyWebhookInboxRepository(
        session_scope=factory.get_scoped_session
    )
    return ReceiveWebhookUseCase(repository)
