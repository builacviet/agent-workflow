"""One dispatch budget shared by trusted host operations in the current context."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import math
import time
from typing import Iterator

_BUDGET: ContextVar[tuple[float, float] | None] = ContextVar(
    "my_wfc_dispatch_budget", default=None
)


def operation_timeout(limit: float) -> float:
    budget = _BUDGET.get()
    if budget is None:
        return limit
    wall_deadline, monotonic_deadline = budget
    remaining = min(
        wall_deadline - time.time(), monotonic_deadline - time.monotonic()
    )
    if remaining <= 0:
        raise TimeoutError(
            "dispatch deadline exceeded; reconciliation required"
        )
    return min(limit, remaining)


def check_deadline() -> None:
    operation_timeout(float("inf"))


@contextmanager
def dispatch_budget(deadline: float) -> Iterator[None]:
    if not math.isfinite(deadline):
        raise ValueError("finite dispatch deadline required")
    remaining = deadline - time.time()
    token = _BUDGET.set((deadline, time.monotonic() + remaining))
    try:
        check_deadline()
        yield
    finally:
        _BUDGET.reset(token)


@contextmanager
def cleanup_budget() -> Iterator[None]:
    """Termination/reconciliation must remain possible after execution expiry."""
    token = _BUDGET.set(None)
    try:
        yield
    finally:
        _BUDGET.reset(token)
