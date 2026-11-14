"""Validated application settings for composition boundaries.

Settings are read when from_env() is called, not at import time. No implicit
.env loading: deployment injects process environment; disposable local tests
may set their environment explicitly.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar
from urllib.parse import unquote, urlsplit
from uuid import UUID

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

    @staticmethod
    def techlead_github(
        environ: Mapping[str, str] | None = None,
    ) -> TechLeadGitHubConfig:
        """Load independent settings only for an authorized GitHub tool call."""
        source = os.environ if environ is None else environ
        try:
            slug = source["TECHLEAD_GITHUB_APP_SLUG"]
            installation_id = _github_id(source["TECHLEAD_GITHUB_INSTALLATION_ID"])
            if (
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?", slug)
                or slug == "builacviet-wfc-coder"
                or str(installation_id) == source.get("WFC_CONTROLLER_INSTALLATION_ID")
            ):
                raise ValueError
            return TechLeadGitHubConfig(
                app_id=_github_id(source["TECHLEAD_GITHUB_APP_ID"]),
                installation_id=installation_id,
                app_slug=slug,
                bot_id=_github_id(source["TECHLEAD_GITHUB_BOT_ID"]),
                key_file=_secret_file(source["TECHLEAD_GITHUB_KEY_FILE"]),
                test_prs=_tl_ids(
                    source.get("TECHLEAD_GITHUB_TEST_PRS", ""), _github_id
                ),
            )
        except (KeyError, ValueError):
            raise ValueError(
                "Tech Lead GitHub connection disabled or invalid"
            ) from None

    @staticmethod
    def techlead_notion(
        environ: Mapping[str, str] | None = None,
    ) -> TechLeadNotionConfig:
        """Load independent settings only for an authorized Notion tool call."""
        source = os.environ if environ is None else environ
        try:
            task_pages = _tl_ids(
                source.get("TECHLEAD_NOTION_TASK_PAGES", ""), _notion_id
            )
            test_pages = _tl_ids(
                source.get("TECHLEAD_NOTION_TEST_PAGES", ""), _notion_id
            )
            if not test_pages <= task_pages:
                raise ValueError
            return TechLeadNotionConfig(
                bot_id=_notion_id(source["TECHLEAD_NOTION_BOT_ID"]),
                token_file=_secret_file(source["TECHLEAD_NOTION_TOKEN_FILE"]),
                task_pages=task_pages,
                test_pages=test_pages,
            )
        except (KeyError, ValueError):
            raise ValueError(
                "Tech Lead Notion connection disabled or invalid"
            ) from None


def _tl_ids(raw: str, parse: Callable[[str], T]) -> frozenset[T]:
    if not raw:
        return frozenset()
    values = raw.split(",")
    if len(values) > 100:
        raise ValueError
    return frozenset(parse(value.strip()) for value in values)


def _github_id(raw: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,18}", raw) or int(raw) > 2**63 - 1:
        raise ValueError
    return int(raw)


def notion_uuid(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})",
        value,
    ):
        return None
    parsed = UUID(value)
    return str(parsed) if parsed.int else None


def _notion_id(raw: str) -> str:
    normalized = notion_uuid(raw)
    if normalized is None:
        raise ValueError
    return normalized


def _secret_file(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts or raw.startswith(("//", "\\\\")):
        raise ValueError
    return path


T = TypeVar("T")


@dataclass(frozen=True)
class TechLeadGitHubConfig:
    app_id: int
    installation_id: int
    app_slug: str
    bot_id: int
    key_file: Path = field(repr=False)
    test_prs: frozenset[int] = frozenset()

    @property
    def bot_login(self) -> str:
        return f"{self.app_slug}[bot]"


@dataclass(frozen=True)
class TechLeadNotionConfig:
    bot_id: str
    token_file: Path = field(repr=False)
    task_pages: frozenset[str] = frozenset()
    test_pages: frozenset[str] = frozenset()
