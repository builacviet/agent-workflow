"""Layer: Infrastructure
Responsibility: Send bounded JSON requests to the fixed GitHub API origin.
Dependencies: Python standard library.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_API = "https://api.github.com"
_MAX_BODY = 2_000_000


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    """Synchronous GitHub API transport with per-request bearer credentials."""

    def request_json(self, method: str, path: str, bearer: str) -> Mapping:
        """Only fixed GitHub API paths. Never follow redirects with bearer credentials."""
        if (
            method not in {"GET", "POST"}
            or not path.startswith("/")
            or "//" in path
            or ".." in path
            or ("?" in path and "#" in path)
            or not isinstance(bearer, str)
            or not bearer
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
            with build_opener(ProxyHandler({}), _NoRedirects()).open(
                request, timeout=8
            ) as response:
                body = response.read(_MAX_BODY + 1)
            if len(body) > _MAX_BODY:
                raise OSError("GitHub API response too large")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise OSError("unexpected GitHub API response")
            return result
        except (HTTPError, URLError, TimeoutError, ValueError, UnicodeError):
            # Never echo response bodies, URLs containing credentials or tokens.
            raise OSError("trusted GitHub API read failed") from None
