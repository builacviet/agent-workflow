"""The transaction decision available to synchronous use cases."""

from types import TracebackType
from typing import Protocol, Self


class IUnitOfWork(Protocol):
    """One scope, with rollback unless commit is requested and the scope succeeds."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None:
        """Request commit on successful exit from the active write scope."""
        ...

    def rollback(self) -> None:
        """Mark the active scope for rollback, canceling any pending commit."""
        ...
