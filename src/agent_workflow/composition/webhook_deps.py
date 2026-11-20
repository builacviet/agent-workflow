"""Dependency assembly for the PostgreSQL webhook use case."""

from agent_workflow.application.webhook.receive_webhook import ReceiveWebhookUseCase
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.infrastructure.database.repositories.webhook_inbox_repository import (
    SqlAlchemyWebhookInboxRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory

_OPERATION_ERROR = "PostgreSQL webhook inbox operation failed"


class _ScopedWebhookInbox:
    """Open a fresh durable database transaction for each delivery."""

    def __init__(self, factory: PostgresFactory) -> None:
        self._factory = factory

    def record(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult:
        # repository_session owns the outer transaction and commits on scope exit.
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            result = SqlAlchemyWebhookInboxRepository(session).record(
                notification, body_digest
            )
        return result


def create_webhook_use_case(
    factory: PostgresFactory,
) -> ReceiveWebhookUseCase:
    """Assemble the validated webhook use case and its durable inbox port."""
    return ReceiveWebhookUseCase(_ScopedWebhookInbox(factory))
