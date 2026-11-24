"""Layer: Interfaces/Webhook
Responsibility: Serve bounded signed webhook HTTP requests without request logging.
Dependencies: Application and Domain errors, webhook receiver, standard HTTP server.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_workflow.application.exceptions import (
    InvalidApplicationRequestError,
    PersistenceUnavailableError,
)
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.interfaces.webhook.github_receiver import (
    DEFAULT_MAX_BODY_BYTES,
    WEBHOOK_PATH,
    GitHubWebhookReceiver,
)


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

    def __init__(
        self,
        address,
        receiver: GitHubWebhookReceiver,
        *,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ):
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
                "POST",
                self.path,
                self.headers.raw_items(),
                body,
            )
        except InvalidApplicationRequestError:
            status = HTTPStatus.BAD_REQUEST
        except DomainError:
            status = HTTPStatus.CONFLICT
        except (PersistenceUnavailableError, OSError):
            status = HTTPStatus.SERVICE_UNAVAILABLE
        except Exception:
            # Unexpected application/transport failures are internal errors.
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        return self._reply(status)

    def do_GET(self) -> None:
        return self._reply(
            HTTPStatus.METHOD_NOT_ALLOWED
            if self.path == WEBHOOK_PATH
            else HTTPStatus.NOT_FOUND
        )

    do_PUT = do_GET
    do_DELETE = do_GET
