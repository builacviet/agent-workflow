"""Deterministic trusted GitHub App authentication regressions."""

import base64
import json
import subprocess

import pytest

from agent_workflow.application.exceptions import (
    RuntimeAdmissionError,
    RuntimeContractError,
    RuntimeExecutionError,
)
from agent_workflow.workers.trusted_git.app_auth import (
    BOT,
    REPOSITORY,
    AppVerifier,
    GitHubApp,
)

BASE = "a" * 40
HEAD = "b" * 40


class Transport:
    def __init__(self, now):
        self.calls = []
        self.now = now
        self.slug = "builacviet-wfc-coder"
        self.head = HEAD
        self.compare_status = "ahead"
        self.bot_login = BOT

    def __call__(self, method, endpoint, token, payload=None):
        self.calls.append((method, endpoint, payload))
        if endpoint == "/app":
            return {"id": 1, "slug": self.slug}
        if endpoint.endswith("/installation"):
            return {"id": 2, "app_id": 1, "suspended_at": None}
        if endpoint.endswith("/access_tokens"):
            from datetime import datetime, timezone

            return {
                "token": "synthetic-only",
                "expires_at": datetime.fromtimestamp(
                    self.now[0] + 3600, timezone.utc
                ).isoformat(),
            }
        if endpoint == "/users/" + BOT:
            return {"id": 123, "login": self.bot_login, "type": "Bot"}
        if "/branches/" in endpoint:
            return {"commit": {"sha": self.head}}
        if "/compare/" in endpoint:
            return {"status": self.compare_status}
        return None


@pytest.fixture
def app(tmp_path):
    secret = tmp_path / ".secret"
    secret.mkdir()
    pem = secret / "fixture.pem"
    pem.write_text("not a real key")
    now = [1000]
    transport = Transport(now)
    client = GitHubApp(
        1,
        2,
        pem,
        transport=transport,
        signer=lambda *args: b"signature",
        clock=lambda: now[0],
    )
    return client, transport, now


def test_jwt_cache_refresh_scope_and_identity(app):
    client, transport, now = app
    claims = json.loads(base64.urlsafe_b64decode(client.jwt().split(".")[1] + "=="))
    assert claims == {"iat": 940, "exp": 1540, "iss": "1"}
    assert client.token() == "synthetic-only"
    assert client.token() == "synthetic-only"
    assert len(transport.calls) == 3
    assert transport.calls[-1][2] == {
        "repositories": ["agent-workflow"],
        "permissions": {"contents": "write", "pull_requests": "write"},
    }
    assert client.identity() == (
        BOT,
        "123+" + BOT + "@users.noreply.github.com",
    )
    now[0] += 3500
    client.token()
    assert (
        sum(endpoint.endswith("/access_tokens") for _, endpoint, _ in transport.calls)
        == 2
    )
    client.revoke()
    assert client._token == ""


def test_app_identity_and_stale_sha_fail_closed(app):
    client, transport, _ = app
    transport.slug = "wrong-app"
    with pytest.raises(RuntimeContractError):
        client.token()
    transport.slug = "builacviet-wfc-coder"
    verifier = AppVerifier(client)
    verifier(REPOSITORY, "codex/test", BASE, HEAD)
    assert (
        transport.calls[-2][1]
        == "/repos/builacviet/agent-workflow/branches/codex%2Ftest"
    )
    with pytest.raises(RuntimeContractError):
        verifier(REPOSITORY, "codex/test", BASE, BASE)
    transport.compare_status = "behind"
    with pytest.raises(RuntimeContractError, match="not an ancestor"):
        verifier(REPOSITORY, "codex/test", BASE, HEAD)
    calls_before_rejection = list(transport.calls)
    for repository in ("other/repository", "builacviet/techlead-review-poc"):
        with pytest.raises(RuntimeAdmissionError, match="repository is not authorized"):
            verifier(repository, "codex/test", BASE, HEAD)
        assert transport.calls == calls_before_rejection


@pytest.mark.parametrize("ids", [(True, 2), (0, 2), (1, -2), ("1", 2)])
def test_invalid_app_ids(app, ids):
    with pytest.raises(RuntimeAdmissionError):
        GitHubApp(*ids, app[0].pem)


def test_app_requires_secret_pem_file(app, tmp_path):
    outside = tmp_path / "outside.pem"
    outside.write_text("synthetic key")
    with pytest.raises(RuntimeAdmissionError, match="PEM must be a file in .secret"):
        GitHubApp(1, 2, outside)


def test_app_bot_identity_mismatch_is_contract_error(app):
    client, transport, _ = app
    transport.bot_login = "other-bot[bot]"
    with pytest.raises(RuntimeContractError, match="bot identity mismatch"):
        client.identity()


def test_malformed_and_expired_installation_tokens(app):
    client, transport, _ = app
    original = transport.__call__

    def broken(method, endpoint, token, payload=None):
        if endpoint.endswith("/access_tokens"):
            return {"token": "synthetic", "expires_at": "1970-01-01T00:00:00Z"}
        return original(method, endpoint, token, payload)

    client.transport = broken
    with pytest.raises(RuntimeContractError, match="invalid installation"):
        client.token()


@pytest.mark.parametrize("nested_error", [RuntimeExecutionError, RuntimeContractError])
def test_request_json_sanitizes_nested_runtime_errors(monkeypatch, nested_error):
    import agent_workflow.workers.trusted_git.app_auth as module

    def fail(_data):
        raise nested_error("private synthetic detail")

    monkeypatch.setattr(module, "_run_http_worker", fail)
    with pytest.raises(RuntimeExecutionError) as error:
        module.request_json("GET", "/app", "synthetic-token")
    assert str(error.value) == (
        "GitHub App request failed or timed out; reconciliation required"
    )
    assert error.value.__suppress_context__
    assert "private" not in str(error.value)


def test_request_json_rejects_invalid_endpoint_as_admission_error(monkeypatch):
    import agent_workflow.workers.trusted_git.app_auth as module

    monkeypatch.setattr(
        module, "_run_http_worker", lambda *_args: pytest.fail("transport was called")
    )
    with pytest.raises(RuntimeAdmissionError, match="invalid GitHub endpoint"):
        module.request_json("GET", "//evil.invalid", "synthetic-token")


def test_app_http_error_never_exposes_token(monkeypatch):
    import agent_workflow.workers.trusted_git.app_auth as module

    def fail(data):
        raise OSError("secret synthetic-token")

    monkeypatch.setattr(module, "_run_http_worker", fail)
    with pytest.raises(RuntimeExecutionError) as error:
        module.request_json("GET", "/app", "synthetic-token")
    assert "synthetic-token" not in str(error.value)


def test_http_worker_nonzero_exit_is_runtime_execution_error(monkeypatch):
    import agent_workflow.workers.trusted_git.app_auth as module

    class Process:
        returncode = 1

        def poll(self):
            return self.returncode

        def communicate(self, *_args, **_kwargs):
            return b"private synthetic output", None

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(RuntimeExecutionError, match="GitHub App request failed"):
        module._run_http_worker(b"synthetic request")


def test_http_worker_termination_failure_is_runtime_contract_error():
    from agent_workflow.workers.trusted_git.app_auth import _stop_http_worker

    class Process:
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def communicate(self, **_kwargs):
            raise subprocess.TimeoutExpired("synthetic-http-worker", 5)

    process = Process()
    with pytest.raises(
        RuntimeContractError,
        match="HTTP worker termination unconfirmed; reconciliation required",
    ):
        _stop_http_worker(process)
    assert process.killed


def test_signing_failure_is_runtime_execution_error(monkeypatch, app):
    import agent_workflow.workers.trusted_git.app_auth as module

    def fail(*_args, **_kwargs):
        raise OSError("private synthetic signer output")

    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(
        RuntimeExecutionError, match="GitHub App signing failed"
    ) as error:
        module.sign_rs256(b"synthetic data", app[0].pem)
    assert error.value.__suppress_context__
    assert "private" not in str(error.value)
