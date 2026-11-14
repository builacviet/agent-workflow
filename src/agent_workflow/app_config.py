"""Validated application settings for composition boundaries.

Settings are read when from_env() is called, not at import time. No implicit
.env loading: deployment injects process environment; disposable local tests
may set their environment explicitly.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from agent_workflow.sandbox_state_paths import validate_sandbox_state_dir


def validate_pg_runtime_dsn(value: object) -> str:
    """Accept only an unambiguous, credentialed TCP psycopg URL."""
    try:
        if (
            not isinstance(value, str)
            or not value
            or re.search(r"\s|[?#\\\\]|%(?![0-9a-fA-F]{2})", value)
        ):
            raise ValueError
        url = urlsplit(value)
        if (
            url.scheme != "postgresql+psycopg"
            or not url.hostname
            or not url.username
            or not url.password
            or not url.path.startswith("/")
            or not url.path[1:]
            or "/" in url.path[1:]
            or url.query
            or url.fragment
            or url.netloc.count("@") != 1
            or url.netloc.split("@")[0].count(":") != 1
            or (url.port is not None and not 1 <= url.port <= 65535)
            or "/" in unquote(url.path[1:])
            or "%" in url.hostname
            or "," in url.hostname
        ):
            raise ValueError
        for part in (url.username, url.password, url.path[1:]):
            decoded = unquote(part, errors="strict")
            if not decoded.strip() or any(
                ord(c) < 32 or ord(c) == 127 for c in decoded
            ):
                raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ValueError(
            "PG_RUNTIME_DSN must be an explicit PostgreSQL psycopg TCP URL "
            "with user, password and database"
        ) from None
    return value


class AppConfig(BaseModel):
    """Typed, immutable settings object; extend here during Config Cleanup."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ci_gate_required: StrictBool = True
    pg_runtime_dsn: str | None = Field(default=None, repr=False)
    sandbox_state_dir: Path = Field(
        default_factory=lambda: Path.home() / ".agent-workflow" / "sandbox-fences"
    )

    def __init__(self, **data: object) -> None:
        if data.get("pg_runtime_dsn") is not None:
            validate_pg_runtime_dsn(data["pg_runtime_dsn"])
        super().__init__(**data)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AppConfig":
        source = os.environ if environ is None else environ
        value = source.get("WFC_CI_GATE_REQUIRED", "1")
        if value not in {"0", "1"}:
            raise ValueError("WFC_CI_GATE_REQUIRED must be 0 or 1")
        dsn = source.get("PG_RUNTIME_DSN")
        if dsn is not None:
            validate_pg_runtime_dsn(dsn)
        state = source.get("WFC_SANDBOX_STATE_DIR")
        try:
            options: dict[str, object] = {
                "ci_gate_required": value == "1",
                "pg_runtime_dsn": dsn,
            }
            if state is not None:
                options["sandbox_state_dir"] = Path(state)
            config = cls(**options)
            validate_sandbox_state_dir(config.sandbox_state_dir)
        except (ValueError, TypeError, OSError):
            raise ValueError(
                "WFC_SANDBOX_STATE_DIR must be a safe, durable local directory"
            ) from None
        return config
