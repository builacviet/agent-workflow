"""Request-scoped correlation without framework or thread-global state."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TypeGuard
from uuid import uuid4

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def is_valid_correlation_id(value: object) -> TypeGuard[str]:
    return type(value) is str and _SAFE_ID.fullmatch(value) is not None


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(correlation_id: str | None) -> Token[str | None]:
    """Set the current context value and return its restoration token."""
    return _correlation_id.set(correlation_id)


@contextmanager
def correlation_scope(
    prefix: str = "cli-", correlation_id: str | None = None
) -> Iterator[str]:
    """Use a valid supplied ID or generate a new one and restore on exit."""
    if not is_valid_correlation_id(correlation_id):
        correlation_id = prefix + uuid4().hex
        if not is_valid_correlation_id(correlation_id):
            raise ValueError("invalid correlation ID prefix")
    token = set_correlation_id(correlation_id)
    try:
        yield correlation_id
    finally:
        _correlation_id.reset(token)
