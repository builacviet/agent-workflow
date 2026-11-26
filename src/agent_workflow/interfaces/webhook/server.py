"""Layer: Interfaces/Webhook
Responsibility: Validate startup configuration and run the webhook HTTP server.
Dependencies: AppConfig, the scoped webhook provider and HTTP adapters.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_workflow.app_config import AppConfig, validate_pg_runtime_dsn
from agent_workflow.composition.webhook_deps import compose_webhook
from agent_workflow.interfaces.webhook.github_receiver import GitHubWebhookReceiver
from agent_workflow.interfaces.webhook.http_server import WebhookHTTPServer


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

    with compose_webhook(AppConfig(pg_runtime_dsn=dsn)) as use_case:
        receiver = GitHubWebhookReceiver(
            use_case=use_case,
            secret=secret,
            installation_id=installation_id,
        )
        server = WebhookHTTPServer(("0.0.0.0", port), receiver)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
