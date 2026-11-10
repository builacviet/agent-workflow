"""Read-only GitHub App API for the trusted Controller CI gate.

No GitHub token, App PEM or outgoing GitHub HTTP client enters an agent sandbox.
Webhook fields are never a source of authentication or CI verdicts.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)

from agent_workflow.integrations.github import REPOSITORY, WorkflowError, validate_sha

_API = "https://api.github.com"
_MAX_BODY = 2_000_000
_MAX_CHECKS = 2_000


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _github_json(method: str, path: str, bearer: str) -> Mapping:
    """Only fixed GitHub API paths. Never follow redirects with bearer credentials."""
    if (
        method not in {"GET", "POST"} or not path.startswith("/")
        or "//" in path or ".." in path or "?" in path and "#" in path
        or not isinstance(bearer, str) or not bearer
    ):
        raise OSError("invalid GitHub API invocation")
    request = Request(
        _API + path,
        data=b"{}" if method == "POST" else None,
        headers={
            "Authorization": "Bearer " + bearer,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-workflow-controller",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with build_opener(ProxyHandler({}), _NoRedirects()).open(request, timeout=8) as response:
            body = response.read(_MAX_BODY + 1)
        if len(body) > _MAX_BODY:
            raise OSError("GitHub API response too large")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise OSError("unexpected GitHub API response")
        return result
    except (HTTPError, URLError, TimeoutError, ValueError, UnicodeError) as exc:
        # Never echo response bodies, URLs containing credentials or tokens.
        raise OSError("trusted GitHub API read failed") from None


class ControllerAppToken:
    """Short-lived App installation token, separate from the trusted worker App."""

    def __init__(
        self,
        *,
        app_id: int,
        installation_id: int,
        pem_path: str | Path,
        request_json: Callable[[str, str, str], Mapping] = _github_json,
        clock: Callable[[], float] = time.time,
    ):
        if (
            type(app_id) is not int or app_id <= 0
            or type(installation_id) is not int or installation_id <= 0
        ):
            raise ValueError("Controller GitHub App and installation IDs required")
        path = Path(pem_path)
        if not path.is_absolute() or path.suffix.lower() != ".pem":
            raise ValueError("absolute GitHub App PEM path required")
        self.app_id, self.installation_id = app_id, installation_id
        self._pem_path = path
        self._request_json = request_json
        self._clock = clock
        self._token: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._token and self._clock() + 60 < self._expires:
                return self._token
            try:
                import jwt  # PyJWT[crypto] only in trusted Controller environment

                key = self._pem_path.read_bytes()
                now = int(self._clock())
                assertion = jwt.encode(
                    {"iat": now - 60, "exp": now + 480, "iss": str(self.app_id)},
                    key,
                    algorithm="RS256",
                )
                response = self._request_json(
                    "POST",
                    f"/app/installations/{self.installation_id}/access_tokens",
                    assertion,
                )
                token = response.get("token")
                expires = response.get("expires_at")
                if not isinstance(token, str) or not token or not isinstance(expires, str):
                    raise ValueError("installation token missing")
                expire_at = datetime.fromisoformat(
                    expires.replace("Z", "+00:00")
                ).timestamp()
                if expire_at <= self._clock() + 60:
                    raise ValueError("installation token already expired")
            except Exception:
                raise OSError("Controller GitHub App authentication unavailable") from None
            self._token, self._expires = token, expire_at
            return token


class ControllerChecksClient:
    """ReadOnlyChecks implementation for ci_gate.verify_ci, with full pagination."""

    def __init__(
        self,
        *,
        installation_id: int,
        token_provider: Callable[[], str],
        repository: str = REPOSITORY,
        request_json: Callable[[str, str, str], Mapping] = _github_json,
    ):
        if type(installation_id) is not int or installation_id <= 0:
            raise ValueError("invalid trusted GitHub installation")
        if repository != REPOSITORY:
            raise ValueError("Controller may read only the configured workflow repository")
        self.installation_id = installation_id
        self._token_provider = token_provider
        self._request_json = request_json

    def _request(self, method: str, path: str) -> Mapping:
        try:
            token = self._token_provider()
            if not isinstance(token, str) or not token:
                raise ValueError("empty installation token")
            result = self._request_json(method, path, token)
            if not isinstance(result, Mapping):
                raise ValueError("GitHub API returned non-object")
            return result
        except (ValueError, TypeError, KeyError):
            raise OSError("trusted GitHub API read unavailable") from None

    def get_pull_request(self, repository: str, number: int) -> Mapping:
        if repository != REPOSITORY or type(number) is not int or number <= 0:
            raise OSError("unapproved GitHub PR read")
        return self._request("GET", f"/repos/{repository}/pulls/{number}")

    def list_check_runs(self, repository: str, sha: str) -> Mapping:
        if repository != REPOSITORY:
            raise OSError("unapproved GitHub checks repository")
        try:
            validate_sha(sha)
        except WorkflowError:
            raise OSError("unapproved GitHub checks SHA") from None

        result: list = []
        total: int | None = None
        for page in range(1, _MAX_CHECKS // 100 + 1):
            payload = self._request(
                "GET",
                f"/repos/{repository}/commits/{sha}/check-runs?per_page=100&page={page}",
            )
            count, chunk = payload.get("total_count"), payload.get("check_runs")
            if (
                type(count) is not int or count < 0 or count > _MAX_CHECKS
                or not isinstance(chunk, list)
                or len(chunk) > 100
                or any(not isinstance(item, Mapping) for item in chunk)
                or (total is not None and total != count)
            ):
                raise OSError("GitHub checks pagination incomplete")
            total = count
            result.extend(chunk)
            if len(result) == total:
                return {"total_count": total, "check_runs": result}
            if not chunk or len(result) > total:
                raise OSError("GitHub checks pagination incomplete")
        raise OSError("GitHub checks exceeded bounded pagination")
