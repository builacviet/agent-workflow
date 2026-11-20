"""PostgreSQL-only composition and lifetime for the webhook HTTP server."""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path

from agent_workflow.app_config import AppConfig, validate_pg_runtime_dsn
from agent_workflow.application.webhook.receive_webhook import ReceiveWebhookUseCase
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.infrastructure.database.repositories.webhook_inbox_repository import (
    SqlAlchemyWebhookInboxRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.integrations.webhook_http import WebhookHTTPServer
from agent_workflow.interfaces.webhook.github_receiver import GitHubWebhookReceiver

_OPERATION_ERROR = "PostgreSQL webhook inbox operation failed"


class _ScopedWebhookInbox:
    """A new Session and durable database transaction for every HTTP request."""

    def __init__(self, factory: PostgresFactory) -> None:
        self._factory = factory

    def record(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult:
        # repository_session owns the OUTER connection transaction and commits
        # after the body exits; scoped_session + session.commit alone does not.
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            result = SqlAlchemyWebhookInboxRepository(session).record(
                notification, body_digest
            )
        return result


def main() -> None:
    """Validate PostgreSQL before binding and close every owned resource."""
    dsn = validate_pg_runtime_dsn(os.environ.get("PG_RUNTIME_DSN"))
    secret_path = Path(os.environ["WFC_WEBHOOK_SECRET_FILE"])
    if not secret_path.is_absolute():
        raise ValueError("webhook secret must be mounted at absolute path")
    secret = secret_path.read_bytes().strip(b"\r\n")
    if len(secret) < 32:
        raise ValueError("use at least 32 random bytes for webhook secret")
    installation_id = int(os.environ["WFC_CONTROLLER_INSTALLATION_ID"])
    port = int(os.environ.get("WFC_WEBHOOK_PORT", "8081"))

    with ExitStack() as resources:
        factory = resources.enter_context(
            PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn))
        )
        receiver = GitHubWebhookReceiver(
            ReceiveWebhookUseCase(_ScopedWebhookInbox(factory)),
            secret=secret,
            installation_id=installation_id,
        )
        server = WebhookHTTPServer(("0.0.0.0", port), receiver)
        resources.callback(server.server_close)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
