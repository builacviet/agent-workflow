"""Compatibility exports for the inbound GitHub webhook adapter."""

import sqlite3
from http import HTTPStatus

from agent_workflow.domain.ci_evidence import WebhookNotification as WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult as InboxResult
from agent_workflow.integrations.github import (
    REPOSITORY as REPOSITORY,
)
from agent_workflow.integrations.github import (
    WorkflowError as WorkflowError,
)
from agent_workflow.integrations.github import (
    validate_sha as validate_sha,
)
from agent_workflow.interfaces.webhook.github_receiver import (
    DEFAULT_MAX_BODY_BYTES as DEFAULT_MAX_BODY_BYTES,
)
from agent_workflow.interfaces.webhook.github_receiver import (
    WEBHOOK_PATH as WEBHOOK_PATH,
)
from agent_workflow.interfaces.webhook.github_receiver import (
    GitHubWebhookReceiver as _GitHubWebhookReceiver,
)

# Preserve this historic import for callers that selected the SQLite adapter.
from agent_workflow.storage.webhook_inbox import WebhookInbox as WebhookInbox


class GitHubWebhookReceiver(_GitHubWebhookReceiver):
    """Keep legacy SQLite adapter failures mapped to retryable HTTP 503."""

    def handle(self, method, path, headers, body):
        try:
            return super().handle(method, path, headers, body)
        except sqlite3.Error:
            return HTTPStatus.SERVICE_UNAVAILABLE


__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "GitHubWebhookReceiver",
    "InboxResult",
    "REPOSITORY",
    "WEBHOOK_PATH",
    "WebhookInbox",
    "WebhookNotification",
    "WorkflowError",
    "validate_sha",
]
