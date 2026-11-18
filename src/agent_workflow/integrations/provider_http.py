"""Bounded HTTPS POST transport for trusted OpenAI-compatible providers.

This module deliberately does not select providers, resolve ambient credentials,
follow redirects, inherit proxy configuration, or execute provider tool calls.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 30.0
_SECRET_REF = re.compile(r"^secret://[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_PATHS = {
    "chat_completions": "/chat/completions",
    "responses": "/responses",
}


class ProviderTransportError(Exception):
    """A sanitized transport failure with a stable, non-sensitive category."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Response:
    status: int
    body: bytes


class _Connection(Protocol):
    sock: socket.socket | None

    def request(
        self, method: str, url: str, body: bytes, headers: Mapping[str, str]
    ) -> None: ...

    def getresponse(self) -> http.client.HTTPResponse: ...

    def close(self) -> None: ...


def _endpoint(value: str, approved_endpoints: frozenset[str]) -> tuple[str, int, str]:
    if not isinstance(value, str) or not isinstance(approved_endpoints, frozenset):
        raise ProviderTransportError("invalid_endpoint")
    try:
        parsed = urlsplit(value)
        declared_port = parsed.port
        port = 443 if declared_port is None else declared_port
        valid = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and 1 <= port <= 65535
            and value in approved_endpoints
        )
    except ValueError:
        valid = False
    if not valid:
        raise ProviderTransportError("invalid_endpoint")
    host = parsed.hostname
    if host is None:
        raise ProviderTransportError("invalid_endpoint")
    path = parsed.path.rstrip("/")
    return host, port, path


def _encode_body(body: object) -> bytes:
    if not isinstance(body, Mapping):
        raise ProviderTransportError("invalid_payload")
    try:
        encoded = json.dumps(
            body, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ProviderTransportError("invalid_payload") from None
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ProviderTransportError("payload_too_large")
    return encoded


def _contains_secret_value(value: object, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, Mapping):
        return any(
            _contains_secret_value(key, secret) or _contains_secret_value(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_value(item, secret) for item in value)
    return False


def _json_contains_secret(text: str, secret: str) -> bool:
    """Inspect decoded JSON strings, including escaped strings and object keys."""
    found = False

    def object_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal found
        result: dict[str, object] = {}
        for name, value in pairs:
            if secret in name or _contains_secret_value(value, secret):
                found = True
            result[name] = value
        return result

    decoded = json.loads(
        text,
        object_pairs_hook=object_hook,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    return found or _contains_secret_value(decoded, secret)


def _read_response(response: http.client.HTTPResponse) -> _Response:
    try:
        length = response.getheader("Content-Length")
        if length is not None and int(length) > MAX_RESPONSE_BYTES:
            raise ProviderTransportError("payload_too_large")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ProviderTransportError("payload_too_large")
        return _Response(response.status, body)
    except ProviderTransportError:
        raise
    except (OSError, http.client.HTTPException, ValueError, OverflowError):
        raise ProviderTransportError("transport_error") from None


def _default_connection(host: str, port: int, timeout: float) -> _Connection:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return http.client.HTTPSConnection(host, port, timeout=timeout, context=context)


def post_json(
    endpoint: str,
    request: object,
    *,
    secret_ref: str,
    resolve_secret: Callable[[str], str],
    approved_endpoints: frozenset[str],
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    connection_factory: Callable[[str, int, float], _Connection] = _default_connection,
) -> str:
    """POST one V1-02D request and return bounded, valid UTF-8 JSON text.

    ``approved_endpoints`` must come from the trusted host's V1-02C policy.
    ``resolve_secret`` is called only after endpoint, path, body and timeout
    validation; no environment or other credential fallback exists.
    """
    host, port, base_path = _endpoint(endpoint, approved_endpoints)
    try:
        protocol = getattr(request, "protocol", None)
        path = getattr(request, "path", None)
        request_body = getattr(request, "body", None)
    except Exception:
        raise ProviderTransportError("invalid_path") from None
    if (
        not isinstance(protocol, str)
        or not isinstance(path, str)
        or protocol not in _PATHS
        or path != _PATHS[protocol]
    ):
        raise ProviderTransportError("invalid_path")
    if (
        isinstance(connect_timeout, bool)
        or not isinstance(connect_timeout, (int, float))
        or not 0 < connect_timeout <= 60
    ):
        raise ProviderTransportError("invalid_timeout")
    if (
        isinstance(read_timeout, bool)
        or not isinstance(read_timeout, (int, float))
        or not 0 < read_timeout <= 300
    ):
        raise ProviderTransportError("invalid_timeout")
    body = _encode_body(request_body)
    if not isinstance(secret_ref, str) or not _SECRET_REF.fullmatch(secret_ref):
        raise ProviderTransportError("invalid_secret_ref")
    if not callable(resolve_secret):
        raise ProviderTransportError("invalid_secret_resolver")
    try:
        key = resolve_secret(secret_ref)
    except Exception:
        raise ProviderTransportError("credential_unavailable") from None
    if (
        not isinstance(key, str)
        or not key
        or not key.isascii()
        or any(ord(char) < 32 or ord(char) == 127 for char in key)
    ):
        raise ProviderTransportError("credential_unavailable")
    try:
        if _json_contains_secret(body.decode("utf-8"), key):
            raise ProviderTransportError("credential_in_payload")
    except ProviderTransportError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProviderTransportError("invalid_payload") from None

    connection: _Connection | None = None
    try:
        connection = connection_factory(host, port, float(connect_timeout))
        # Direct HTTPSConnection has no environment proxy discovery. Its default
        # SSL context verifies the certificate and hostname.
        connection.request(
            "POST",
            f"{base_path}{path}",
            body,
            {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Length": str(len(body)),
                "Connection": "close",
            },
        )
        if connection.sock is not None:
            connection.sock.settimeout(float(read_timeout))
        response = _read_response(connection.getresponse())
    except ProviderTransportError:
        raise
    except (OSError, ssl.SSLError, socket.timeout, http.client.HTTPException):
        raise ProviderTransportError("transport_error") from None
    except Exception:
        # Injected connections and low-level TLS implementations may expose
        # exception messages containing headers or endpoint details.
        raise ProviderTransportError("transport_error") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    if not 200 <= response.status < 300:
        raise ProviderTransportError("http_error")
    try:
        text = response.body.decode("utf-8", errors="strict")
        contains_secret = _json_contains_secret(text, key)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProviderTransportError("invalid_payload") from None
    if contains_secret:
        raise ProviderTransportError("credential_in_response")
    return text
