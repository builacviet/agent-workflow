"""Offline socket regression for the signed, bounded webhook transport."""

import hmac
import http.client
import json
import threading
import uuid
from contextlib import contextmanager
from http import HTTPStatus
from unittest.mock import MagicMock

import pytest

from agent_workflow.composition import webhook_entrypoint
from agent_workflow.integrations.github_webhook import GitHubWebhookReceiver
from agent_workflow.integrations.webhook_http import WebhookHTTPServer
from agent_workflow.storage.postgres_connection import PostgresUnavailableError
from agent_workflow.storage.webhook_inbox import InboxResult, WebhookInbox


@contextmanager
def endpoint(tmp_path, *, max_body=1024):
    receiver = GitHubWebhookReceiver(
        WebhookInbox(tmp_path / "test.db"),
        secret=b"z" * 32,
        installation_id=17,
    )
    server = WebhookHTTPServer(("127.0.0.1", 0), receiver, max_body_bytes=max_body)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def send(port, method="POST", path="/webhooks/github", body=b"x", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(method, path, body, headers=headers or {})
    response = conn.getresponse()
    code = response.status
    response.read()
    conn.close()
    return code


def signed(body):
    return {
        "X-Hub-Signature-256": "sha256=" + hmac.digest(b"z" * 32, body, "sha256").hex(),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": str(uuid.uuid4()),
    }


def payload():
    return json.dumps(
        {
            "action": "synchronize",
            "repository": {"full_name": "builacviet/agent-workflow"},
            "installation": {"id": 17},
            "number": 42,
            "pull_request": {"head": {"sha": "a" * 40}},
        }
    ).encode()


def test_real_http_accepts_signed_replay_once(tmp_path):
    with endpoint(tmp_path) as port:
        body = payload()
        assert send(port, body=body, headers=signed(body)) == 202
        assert send(port, body=body, headers=signed(body)) == 200


def test_rejects_bad_signature_wrong_path_and_big_body(tmp_path):
    with endpoint(tmp_path) as port:
        body = payload()
        headers = signed(body)
        headers["X-Hub-Signature-256"] = "sha256=" + "a" * 64
        assert send(port, body=body, headers=headers) == 403
        assert send(port, path="/mcp", body=body, headers=signed(body)) == 404
        assert send(port, body=b"x" * 2048, headers={}) == 413


def test_http_method_never_forwards_to_mcp(tmp_path):
    with endpoint(tmp_path) as port:
        assert send(port, method="GET", body=b"") == HTTPStatus.METHOD_NOT_ALLOWED


def _main_environment(monkeypatch, tmp_path):
    secret = tmp_path / "secret"
    secret.write_bytes(b"z" * 32)
    monkeypatch.setenv("WFC_WEBHOOK_SECRET_FILE", str(secret))
    monkeypatch.setenv("WFC_CONTROLLER_INSTALLATION_ID", "17")
    monkeypatch.setenv("WFC_WEBHOOK_PORT", "0")
    monkeypatch.delenv("PG_RUNTIME_DSN", raising=False)
    monkeypatch.delenv("WFC_WEBHOOK_DB_PATH", raising=False)


def test_main_selects_pg_only_and_closes_factory(monkeypatch, tmp_path):
    _main_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@localhost/db")
    factory = MagicMock()
    factory.__enter__.return_value = factory
    monkeypatch.setattr(webhook_entrypoint.PostgresFactory, "open", lambda _: factory)
    server = MagicMock()
    receivers = []

    def capture_server(_, receiver):
        receivers.append(receiver)
        return server

    monkeypatch.setattr(webhook_entrypoint, "WebhookHTTPServer", capture_server)

    webhook_entrypoint.main()

    assert len(receivers) == 1
    assert receivers[0]._inbox._factory is factory
    factory.__exit__.assert_called_once()
    server.server_close.assert_called_once()


@pytest.mark.parametrize("failure", ["bind", "serve"])
def test_main_closes_factory_on_server_failure(monkeypatch, tmp_path, failure):
    _main_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@localhost/db")
    factory = MagicMock()
    factory.__enter__.return_value = factory
    monkeypatch.setattr(webhook_entrypoint.PostgresFactory, "open", lambda _: factory)
    server = MagicMock()
    if failure == "serve":
        server.serve_forever.side_effect = RuntimeError("serve failed")

    def build_server(*_):
        if failure == "bind":
            raise OSError("bind failed")
        return server

    monkeypatch.setattr(webhook_entrypoint, "WebhookHTTPServer", build_server)
    with pytest.raises((OSError, RuntimeError)):
        webhook_entrypoint.main()
    factory.__exit__.assert_called_once()
    if failure == "serve":
        server.server_close.assert_called_once()


def test_missing_dsn_fails_before_socket(monkeypatch, tmp_path):
    _main_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        webhook_entrypoint.PostgresFactory, "open", lambda _: pytest.fail("PG opened")
    )
    monkeypatch.setattr(
        webhook_entrypoint, "WebhookHTTPServer", lambda *a: pytest.fail("socket opened")
    )
    with pytest.raises(ValueError, match="PG_RUNTIME_DSN"):
        webhook_entrypoint.main()


@pytest.mark.parametrize(
    "dsn", ["", "sqlite:///wrong", "postgresql://user:pass@host/db"]
)
def test_invalid_explicit_dsn_fails_before_socket(monkeypatch, tmp_path, dsn):
    _main_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PG_RUNTIME_DSN", dsn)
    monkeypatch.setattr(
        webhook_entrypoint, "WebhookHTTPServer", lambda *a: pytest.fail("socket opened")
    )
    with pytest.raises(ValueError, match="PG_RUNTIME_DSN"):
        webhook_entrypoint.main()


def test_unavailable_pg_fails_before_socket(monkeypatch, tmp_path):
    _main_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@localhost/db")

    def fail_open(_):
        raise PostgresUnavailableError(
            "PostgreSQL connection or schema readiness check failed"
        )

    monkeypatch.setattr(webhook_entrypoint.PostgresFactory, "open", fail_open)
    monkeypatch.setattr(
        webhook_entrypoint, "WebhookHTTPServer", lambda *a: pytest.fail("socket opened")
    )
    with pytest.raises(PostgresUnavailableError):
        webhook_entrypoint.main()


def test_pg_persistence_failure_returns_503_and_retry_succeeds():
    class FailingInbox:
        def __init__(self):
            self.calls = 0

        def record(self, notification, digest):
            self.calls += 1
            if self.calls == 1:
                raise PostgresUnavailableError("private database detail")
            return InboxResult.INSERTED

    inbox = FailingInbox()
    receiver = webhook_entrypoint._PostgresReceiver(
        inbox, secret=b"z" * 32, installation_id=17
    )
    server = WebhookHTTPServer(("127.0.0.1", 0), receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = payload()
        headers = signed(body)
        assert send(server.server_address[1], body=body, headers=headers) == 503
        assert send(server.server_address[1], body=body, headers=headers) == 202
        assert inbox.calls == 2
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
