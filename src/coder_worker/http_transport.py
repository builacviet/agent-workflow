"""Trusted one-request worker. Parent process owns the total HTTP deadline."""

from __future__ import annotations

import json
import sys
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_ORIGIN = "https://api.github.com"
MAX_RESPONSE_BYTES = 2_000_000
MAX_INPUT_BYTES = 16_000_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        raise ValueError("redirect refused")


def request(
    method: str, endpoint: str, token: str, payload: dict | None
) -> bytes:
    if not endpoint.startswith("/") or endpoint.startswith("//"):
        raise ValueError("invalid endpoint")
    data = None if payload is None else json.dumps(payload).encode()
    outgoing = Request(
        API_ORIGIN + endpoint,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "Content-Type": "application/json",
            "User-Agent": "my-wfc-phase31",
        },
    )
    # Socket timeout is only a secondary guard. Parent kills/reaps this worker
    # on the total deadline, including DNS/connect/TLS/headers/body/proxy waits.
    with build_opener(NoRedirect()).open(outgoing, timeout=20) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return body


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("request too large")
        data = json.loads(raw)
        body = request(
            data["method"], data["endpoint"], data["token"], data["payload"]
        )
        sys.stdout.buffer.write(body)
    except Exception:
        # No traceback, headers, response or credentials on worker stderr.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
