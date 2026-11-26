"""Layer: Composition
Responsibility: Assemble the webhook use case and scope its database factory.
Dependencies: AppConfig, the Application use case and PostgreSQL adapters.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from agent_workflow.app_config import AppConfig
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
    return ReceiveWebhookUseCase(inbox=repository)


@contextmanager
def compose_webhook(config: AppConfig) -> Iterator[ReceiveWebhookUseCase]:
    with PostgresFactory.open(config) as factory:
        yield create_webhook_use_case(factory)
