"""Offline socket regression for the signed, bounded webhook transport."""
from contextlib import contextmanager
from http import HTTPStatus
import http.client
import hmac
import json
import threading
import uuid

from agent_workflow.integrations.github_webhook import GitHubWebhookReceiver
from agent_workflow.integrations.webhook_http import WebhookHTTPServer
from agent_workflow.storage.webhook_inbox import WebhookInbox


@contextmanager
def endpoint(tmp_path, *, max_body=1024):
    receiver = GitHubWebhookReceiver(
        WebhookInbox(tmp_path / "test.db"), secret=b"z" * 32,
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
    return json.dumps({
        "action": "synchronize",
        "repository": {"full_name": "builacviet/agent-workflow"},
        "installation": {"id": 17},
        "number": 42,
        "pull_request": {"head": {"sha": "a" * 40}},
    }).encode()


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
