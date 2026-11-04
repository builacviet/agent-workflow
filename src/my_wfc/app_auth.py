"""GitHub App credentials stay in the trusted process, never in coder snapshots."""

from __future__ import annotations

import base64
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from my_wfc.budget import check_deadline, operation_timeout
from my_wfc.github import GitHubVerifier, WorkflowError

BOT = "builacviet-wfc-coder[bot]"
REPOSITORY = "builacviet/techlead-review-poc"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise WorkflowError("GitHub redirect refused")


def request_json(
    method: str, endpoint: str, token: str, payload: dict | None = None
) -> Any:
    if not endpoint.startswith("/") or endpoint.startswith("//"):
        raise WorkflowError("invalid GitHub endpoint")
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        "https://api.github.com" + endpoint,
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
    try:
        with build_opener(NoRedirect()).open(
            request, timeout=operation_timeout(20)
        ) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise WorkflowError("GitHub response too large")
        return json.loads(body) if body else None
    except (HTTPError, URLError, OSError, ValueError):
        raise WorkflowError("GitHub App request failed") from None


def sign_rs256(data: bytes, key: Path) -> bytes:
    try:
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(key)],
            input=data,
            capture_output=True,
            check=True,
            timeout=operation_timeout(10),
        ).stdout
        check_deadline()
        return result
    except (OSError, subprocess.SubprocessError):
        raise WorkflowError("GitHub App signing failed") from None


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class GitHubApp:
    def __init__(
        self,
        app_id: int,
        installation_id: int,
        pem: Path,
        *,
        transport: Callable = request_json,
        signer: Callable = sign_rs256,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (app_id, installation_id)
        ):
            raise WorkflowError(
                "positive integer App and installation IDs required"
            )
        pem = pem.resolve(strict=True)
        if (
            any("agent_workflow" in part.lower() for part in pem.parts)
            or pem.suffix != ".pem"
            or pem.parent.name != ".secret"
            or not pem.is_file()
        ):
            raise WorkflowError("PEM must be a file in .secret")
        self.app_id, self.installation_id, self.pem = (
            app_id,
            installation_id,
            pem,
        )
        self.transport, self.signer, self.clock = transport, signer, clock
        self._token, self._expires = "", 0.0

    def _request(
        self,
        method: str,
        endpoint: str,
        token: str,
        payload: dict | None = None,
    ) -> Any:
        check_deadline()
        result = self.transport(method, endpoint, token, payload)
        check_deadline()
        return result

    def jwt(self) -> str:
        now = int(self.clock())
        header = b64(b'{"alg":"RS256","typ":"JWT"}')
        claims = b64(
            json.dumps(
                {"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)}
            ).encode()
        )
        message = (header + "." + claims).encode()
        return message.decode() + "." + b64(self.signer(message, self.pem))

    def token(self) -> str:
        if self._expires > self.clock() + 120:
            return self._token
        jwt = self.jwt()
        app = self._request("GET", "/app", jwt)
        installation = self._request(
            "GET", f"/repos/{REPOSITORY}/installation", jwt
        )
        if (
            app.get("id") != self.app_id
            or app.get("slug") != BOT[:-5]
            or installation.get("id") != self.installation_id
            or installation.get("app_id") != self.app_id
            or installation.get("suspended_at") is not None
        ):
            raise WorkflowError("GitHub App or installation identity mismatch")
        result = self._request(
            "POST",
            f"/app/installations/{self.installation_id}/access_tokens",
            jwt,
            {
                "repositories": [REPOSITORY.split("/")[1]],
                "permissions": {"contents": "write", "pull_requests": "write"},
            },
        )
        try:
            expires = datetime.fromisoformat(
                result["expires_at"].replace("Z", "+00:00")
            ).timestamp()
            token = result["token"]
            if (
                not isinstance(token, str)
                or not token
                or not self.clock() + 120 < expires <= self.clock() + 3660
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise WorkflowError(
                "invalid installation token response"
            ) from None
        self._token, self._expires = token, expires
        return token

    def api(
        self, method: str, endpoint: str, payload: dict | None = None
    ) -> Any:
        return self._request(method, endpoint, self.token(), payload)

    def identity(self) -> tuple[str, str]:
        bot = self.api("GET", "/users/" + BOT)
        if (
            bot.get("login") != BOT
            or bot.get("type") != "Bot"
            or type(bot.get("id")) is not int
        ):
            raise WorkflowError("GitHub bot identity mismatch")
        return BOT, f"{bot['id']}+{BOT}@users.noreply.github.com"

    def revoke(self) -> None:
        if self._token:
            try:
                self._request("DELETE", "/installation/token", self._token)
            finally:
                self._token, self._expires = "", 0.0


class AppVerifier(GitHubVerifier):
    def __init__(self, app: GitHubApp) -> None:
        super().__init__({REPOSITORY})
        self.app = app

    def _get(self, endpoint: str) -> Any:
        return self.app.api("GET", "/" + endpoint)
