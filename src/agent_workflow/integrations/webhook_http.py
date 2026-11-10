"""Bounded webhook HTTP transport. No MCP/OAuth or App credentials in process."""
from __future__ import annotations
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import threading
import time

from agent_workflow.integrations.github_webhook import (
    DEFAULT_MAX_BODY_BYTES, GitHubWebhookReceiver, WEBHOOK_PATH,
)
from agent_workflow.storage.webhook_inbox import WebhookInbox


class _RateLimit:
    def __init__(self, limit: int = 120):
        self._times = deque()
        self._limit = limit
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            while self._times and self._times[0] <= now - 60:
                self._times.popleft()
            if len(self._times) >= self._limit:
                return False
            self._times.append(now)
            return True


class WebhookHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, receiver: GitHubWebhookReceiver, *,
                 max_body_bytes: int = DEFAULT_MAX_BODY_BYTES):
        if type(max_body_bytes) is not int or max_body_bytes <= 0:
            raise ValueError("invalid webhook body cap")
        self.receiver = receiver
        self.max_body_bytes = max_body_bytes
        self.rate = _RateLimit()
        super().__init__(address, WebhookHandler)


class WebhookHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: WebhookHTTPServer

    def log_message(self, *args):
        # No request URL, signature or raw-body logging.
        return

    def _reply(self, status: HTTPStatus) -> None:
        self.send_response(int(status))
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_POST(self) -> None:
        if self.path != WEBHOOK_PATH:
            return self._reply(HTTPStatus.NOT_FOUND)
        if not self.server.rate.allow():
            return self._reply(HTTPStatus.TOO_MANY_REQUESTS)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
            return self._reply(HTTPStatus.BAD_REQUEST)
        raw = lengths[0]
        if not raw.isdecimal() or len(raw) > 10:
            return self._reply(HTTPStatus.BAD_REQUEST)
        length = int(raw)
        if length < 1:
            return self._reply(HTTPStatus.BAD_REQUEST)
        if length > self.server.max_body_bytes:
            return self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        body = self.rfile.read(length)
        if len(body) != length:
            return self._reply(HTTPStatus.BAD_REQUEST)
        try:
            status = self.server.receiver.handle(
                "POST", self.path, self.headers.raw_items(), body,
            )
        except (OSError, ValueError):
            status = HTTPStatus.SERVICE_UNAVAILABLE
        return self._reply(status)

    def do_GET(self) -> None:
        return self._reply(
            HTTPStatus.METHOD_NOT_ALLOWED
            if self.path == WEBHOOK_PATH else HTTPStatus.NOT_FOUND
        )

    do_PUT = do_GET
    do_DELETE = do_GET


def main() -> None:
    db = os.environ.get("AGENT_WORKFLOW_WEBHOOK_DATABASE_URL") or os.environ["WFC_WEBHOOK_DB_PATH"]
    if db.startswith(("postgresql://", "postgres://")):
        from agent_workflow.storage.postgres_webhook import PostgresWebhookInbox
        inbox = PostgresWebhookInbox(db)
    else:
        inbox = WebhookInbox(db)
    secret_path = Path(os.environ["WFC_WEBHOOK_SECRET_FILE"])
    if not secret_path.is_absolute():
        raise ValueError("webhook secret must be mounted at absolute path")
    secret = secret_path.read_bytes().strip(b"\r\n")
    if len(secret) < 32:
        raise ValueError("use at least 32 random bytes for webhook secret")
    receiver = GitHubWebhookReceiver(
        inbox, secret=secret,
        installation_id=int(os.environ["WFC_CONTROLLER_INSTALLATION_ID"]),
    )
    port = int(os.environ.get("WFC_WEBHOOK_PORT", "8081"))
    server = WebhookHTTPServer(("0.0.0.0", port), receiver)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
