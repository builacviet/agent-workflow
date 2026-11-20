"""Persistence boundary for validated webhook receipt."""

from typing import Protocol

from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult


class IWebhookInboxRepository(Protocol):
    """Durably record one validated webhook notification."""

    def record(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult: ...
