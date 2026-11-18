"""Offline security and data-flow tests for the trusted provider transport."""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from typing import Any

import pytest

from agent_workflow.integrations.provider_http import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    ProviderTransportError,
    post_json,
)
from agent_workflow.orchestration.provider_protocols import (
    encode_request,
    normalize_response,
)
from agent_workflow.orchestration.role_profiles import (
    CapabilityCatalog,
    ModelCapabilities,
    ProviderConfig,
    RoleProfile,
    RoleProfiles,
    StructuredOutput,
    TrustedTaskContext,
)

ENDPOINT = "https://provider.test/v1"
KEY = "fake-test-key-never-real"
PATHS = {
    "chat_completions": "/chat/completions",
    "responses": "/responses",
}
SCHEMA = StructuredOutput(
    {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
)


@dataclass
class FakeResponse:
    status: int = 200
    body: bytes = b'{"ok":true}'
    declared_length: str | None = None

    def getheader(self, name: str) -> str | None:
        if name.lower() == "content-length" and self.declared_length is not None:
            return self.declared_length
        if name.lower() == "content-length":
            return str(len(self.body))
        return None

    def getheaders(self) -> list[tuple[str, str]]:
        return [("Content-Type", "application/json")]

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


class FakeConnection:
    def __init__(self, response: FakeResponse, failure: Exception | None = None):
        self.response = response
        self.failure = failure
        self.sock: Any = _FakeSocket()
        self.calls: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, url: str, body: bytes, headers: Any) -> None:
        self.calls.append((method, url, body, dict(headers)))
        if self.failure:
            raise self.failure

    def getresponse(self) -> FakeResponse:
        return self.response  # type: ignore[return-value]

    def close(self) -> None:
        self.closed = True


class _FakeSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class Harness:
    def __init__(
        self, response: FakeResponse | None = None, failure: Exception | None = None
    ):
        self.connection = FakeConnection(response or FakeResponse(), failure)
        self.factories: list[tuple[str, int, float]] = []
        self.secret_refs: list[str] = []

    def factory(self, host: str, port: int, timeout: float) -> FakeConnection:
        self.factories.append((host, port, timeout))
        return self.connection

    def resolve(self, reference: str) -> str:
        self.secret_refs.append(reference)
        return KEY


@dataclass(frozen=True)
class Request:
    protocol: str
    path: str
    body: dict[str, object]


def _request(protocol: str = "chat_completions") -> Request:
    return Request(protocol, PATHS[protocol], {"model": "offline-test", "input": "hi"})


def _post(
    harness: Harness,
    request: object | None = None,
    *,
    endpoint: str = ENDPOINT,
    secret_ref: str = "secret://providers/test",
    approved: frozenset[str] = frozenset({ENDPOINT}),
    **kwargs: Any,
) -> str:
    return post_json(
        endpoint,
        request or _request(),
        secret_ref=secret_ref,
        resolve_secret=harness.resolve,
        approved_endpoints=approved,
        connection_factory=harness.factory,
        **kwargs,
    )


def _config(protocol: str) -> RoleProfiles:
    capabilities = frozenset({"json_schema"})
    provider = ProviderConfig(
        ENDPOINT,
        protocol,  # type: ignore[arg-type]
        "secret://providers/test",
        capabilities,
    )
    model = ModelCapabilities(
        provider="alpha",
        model="offline-test",
        protocols=frozenset({protocol}),  # type: ignore[arg-type]
        approved_base_urls=frozenset({ENDPOINT}),
        capabilities=capabilities,
        reasoning_levels=frozenset(),
        effort_levels={},
        default_omits_effort=True,
    )
    return RoleProfiles(
        {
            "dev": RoleProfile("alpha", "offline-test", "default"),
            "qc": RoleProfile("alpha", "offline-test", "default"),
            "tl-reviewer": RoleProfile("alpha", "offline-test", "default"),
        },
        {"alpha": provider},
        CapabilityCatalog("v1", {("alpha", "offline-test"): model}),
    )


def _provider_reply(protocol: str) -> bytes:
    content = '{"answer":"offline success"}'
    if protocol == "chat_completions":
        value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ]
        }
    else:
        value = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": content}],
                }
            ],
        }
    return json.dumps(value).encode()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
def test_offline_role_protocol_transport_normalization_flow(protocol: str) -> None:
    profiles = _config(protocol)
    context = TrustedTaskContext("TASK-42", "dev", "code", True)
    protocol_request = encode_request(
        profiles, context, "Return an answer.", structured_output=SCHEMA
    )
    harness = Harness(FakeResponse(body=_provider_reply(protocol)))

    response_text = _post(
        harness, protocol_request, secret_ref=profiles.providers["alpha"].api_key_ref
    )
    normalized = normalize_response(protocol, response_text, structured_output=SCHEMA)  # type: ignore[arg-type]

    assert normalized.structured_output == {"answer": "offline success"}
    assert normalized.tool_calls == ()
    assert len(harness.connection.calls) == 1
    assert harness.connection.calls[0][0:2] == ("POST", f"/v1{PATHS[protocol]}")
    assert harness.secret_refs == ["secret://providers/test"]


def test_valid_post_is_direct_single_attempt_with_auth_and_explicit_timeouts() -> None:
    harness = Harness(FakeResponse(body=b'{"ok":true}'))

    assert _post(harness, connect_timeout=2, read_timeout=7) == '{"ok":true}'
    assert harness.factories == [("provider.test", 443, 2.0)]
    assert harness.connection.calls[0][0] == "POST"
    assert harness.connection.calls[0][3]["Authorization"] == f"Bearer {KEY}"
    assert harness.connection.sock.timeouts == [7.0]
    assert harness.connection.closed


def test_default_https_connection_does_not_inherit_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_workflow.integrations import provider_http

    harness = Harness(FakeResponse(body=b'{"ok":true}'))
    seen: list[tuple[str, int, float, bool]] = []

    def direct_connection(host: str, port: int, *, timeout: float, context: Any):
        seen.append((host, port, timeout, context.verify_mode == ssl.CERT_REQUIRED))
        return harness.connection

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1234")
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:1234")
    monkeypatch.setattr(provider_http.http.client, "HTTPSConnection", direct_connection)

    provider_http.post_json(
        ENDPOINT,
        _request(),
        secret_ref="secret://providers/test",
        resolve_secret=harness.resolve,
        approved_endpoints=frozenset({ENDPOINT}),
    )

    assert seen == [("provider.test", 443, 5.0, True)]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://provider.test/v1",
        "https://user:pass@provider.test/v1",
        "https://provider.test/v1?override=x",
        "https://provider.test/v1#fragment",
        "https://provider.test:99999/v1",
    ],
)
def test_untrusted_or_malformed_endpoint_fails_before_secret_or_io(
    endpoint: str,
) -> None:
    harness = Harness()
    with pytest.raises(ProviderTransportError) as error:
        _post(harness, endpoint=endpoint)
    assert error.value.code == "invalid_endpoint"
    assert not harness.secret_refs
    assert not harness.factories


def test_non_allowlisted_endpoint_fails_before_io() -> None:
    harness = Harness()
    with pytest.raises(ProviderTransportError, match="invalid_endpoint"):
        _post(harness, endpoint="https://other.test/v1")
    assert not harness.factories


@pytest.mark.parametrize(
    "protocol_request",
    [Request("chat_completions", "/responses", {}), Request("vendor", "/x", {})],
)
def test_wrong_protocol_path_fails_before_secret_or_io(
    protocol_request: Request,
) -> None:
    harness = Harness()
    with pytest.raises(ProviderTransportError) as error:
        _post(harness, protocol_request)
    assert error.value.code == "invalid_path"
    assert not harness.secret_refs
    assert not harness.factories


@pytest.mark.parametrize("secret_ref", ["plain-key", "secret://", "secret://bad?x"])
def test_invalid_secret_reference_never_resolves(secret_ref: str) -> None:
    harness = Harness()
    with pytest.raises(ProviderTransportError) as error:
        _post(harness, secret_ref=secret_ref)
    assert error.value.code == "invalid_secret_ref"
    assert not harness.secret_refs
    assert not harness.factories


def test_secret_resolution_failure_is_sanitized() -> None:
    harness = Harness()

    def fail(_reference: str) -> str:
        raise RuntimeError(f"secret leaked: {KEY}")

    with pytest.raises(ProviderTransportError) as error:
        post_json(
            ENDPOINT,
            _request(),
            secret_ref="secret://providers/test",
            resolve_secret=fail,
            approved_endpoints=frozenset({ENDPOINT}),
            connection_factory=harness.factory,
        )
    assert str(error.value) == "credential_unavailable"
    assert KEY not in repr(error.value)
    assert not harness.factories


@pytest.mark.parametrize("invalid_key", ["", "bad\r\nheader", "non-ascii-é"])
def test_invalid_resolved_credentials_fail_before_network(invalid_key: str) -> None:
    harness = Harness()

    def resolve(_reference: str) -> str:
        return invalid_key

    with pytest.raises(ProviderTransportError, match="credential_unavailable"):
        post_json(
            ENDPOINT,
            _request(),
            secret_ref="secret://providers/test",
            resolve_secret=resolve,
            approved_endpoints=frozenset({ENDPOINT}),
            connection_factory=harness.factory,
        )
    assert not harness.factories


def test_credential_cannot_be_in_request_body_or_response() -> None:
    harness = Harness()
    request = Request("responses", "/responses", {"prompt": KEY})
    with pytest.raises(ProviderTransportError, match="credential_in_payload"):
        _post(harness, request)
    assert not harness.factories

    harness = Harness(FakeResponse(body=json.dumps({"echo": KEY}).encode()))
    with pytest.raises(ProviderTransportError, match="credential_in_response") as error:
        _post(harness)
    assert KEY not in str(error.value)


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 429, 500, 503])
def test_redirect_and_http_errors_are_not_followed_or_exposed(status: int) -> None:
    harness = Harness(FakeResponse(status=status, body=KEY.encode()))
    with pytest.raises(ProviderTransportError) as error:
        _post(harness)
    assert error.value.code == "http_error"
    assert KEY not in str(error.value)
    assert len(harness.connection.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(body=b"x" * (MAX_RESPONSE_BYTES + 1)),
        FakeResponse(body=b"{}", declared_length=str(MAX_RESPONSE_BYTES + 1)),
    ],
)
def test_oversized_response_is_rejected_bounded(response: FakeResponse) -> None:
    harness = Harness(response)
    with pytest.raises(ProviderTransportError, match="payload_too_large"):
        _post(harness)


@pytest.mark.parametrize("body", [b"not-json", b"\xff", b"NaN"])
def test_malformed_utf8_or_json_is_rejected(body: bytes) -> None:
    with pytest.raises(ProviderTransportError, match="invalid_payload"):
        _post(Harness(FakeResponse(body=body)))


def test_oversized_request_is_rejected_without_io() -> None:
    harness = Harness()
    request = Request("responses", "/responses", {"input": "x" * MAX_REQUEST_BYTES})
    with pytest.raises(ProviderTransportError, match="payload_too_large"):
        _post(harness, request)
    assert not harness.factories


@pytest.mark.parametrize(
    "failure",
    [TimeoutError(KEY), socket.timeout(KEY), ssl.SSLError(KEY), OSError(KEY)],
)
def test_transport_and_tls_errors_are_sanitized_without_retry(
    failure: Exception,
) -> None:
    harness = Harness(failure=failure)
    with pytest.raises(ProviderTransportError) as error:
        _post(harness)
    assert error.value.code == "transport_error"
    assert KEY not in repr(error.value)
    assert len(harness.connection.calls) == 1
    assert harness.connection.closed


@pytest.mark.parametrize("timeout", [0, -1, 61, float("inf"), True])
def test_invalid_connect_timeout_fails_before_secret_resolution(timeout: float) -> None:
    harness = Harness()
    with pytest.raises(ProviderTransportError, match="invalid_timeout"):
        _post(harness, connect_timeout=timeout)
    assert not harness.secret_refs
    assert not harness.factories


def test_no_logs_are_emitted_and_error_text_never_contains_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness(FakeResponse(status=401, body=KEY.encode()))
    with pytest.raises(ProviderTransportError) as error:
        _post(harness)
    assert KEY not in str(error.value)
    assert not caplog.records
