"""Layer: Infrastructure
Responsibility: Authenticate the Controller App and cache its installation token.
Dependencies: Injected GitHub JSON requests and optional PyJWT at authentication time.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path


class ControllerAppToken:
    """Short-lived App installation token, separate from the trusted worker App."""

    def __init__(
        self,
        *,
        app_id: int,
        installation_id: int,
        pem_path: str | Path,
        request_json: Callable[[str, str, str], Mapping],
        clock: Callable[[], float] = time.time,
    ):
        if (
            type(app_id) is not int
            or app_id <= 0
            or type(installation_id) is not int
            or installation_id <= 0
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
                if (
                    not isinstance(token, str)
                    or not token
                    or not isinstance(expires, str)
                ):
                    raise ValueError("installation token missing")
                expire_at = datetime.fromisoformat(
                    expires.replace("Z", "+00:00")
                ).timestamp()
                if expire_at <= self._clock() + 60:
                    raise ValueError("installation token already expired")
            except Exception:
                raise OSError(
                    "Controller GitHub App authentication unavailable"
                ) from None
            self._token, self._expires = token, expire_at
            return token
