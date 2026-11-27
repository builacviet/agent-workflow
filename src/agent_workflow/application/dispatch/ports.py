"""Layer: Application
Responsibility: Define contracts for a leadership-gated dispatch pass.
Dependencies: Python standard library only.
"""

from dataclasses import dataclass
from typing import Protocol, TypedDict, TypeVar


@dataclass(frozen=True, slots=True)
class DispatchClaim:
    dispatch_id: str
    task_id: str
    input_sha: str
    deadline: float
    round_number: int


class CoderAssignment(TypedDict):
    """Fields used at dispatch time. The Worker also receives the full assignment."""

    task_id: str
    completed_review_rounds: int
    status: str
    current_head_sha: str | None
    base_sha: str


_Assignment = TypeVar("_Assignment", bound=CoderAssignment, covariant=True)


class IDispatchLeadership(Protocol):
    def try_acquire(self) -> bool: ...

    def is_held(self) -> bool: ...

    def release(self) -> bool: ...


class IDispatchRepository(Protocol):
    def claim(
        self, task_ids: set[str], timeout: int, *, track_pre_launch: bool
    ) -> DispatchClaim | None: ...

    def finish(self, dispatch_id: str, sha: str) -> None: ...

    def block(self, dispatch_id: str) -> None: ...


class ICoderAssignmentReader(Protocol[_Assignment]):
    def get_coder_assignment(self, task_id: str) -> _Assignment: ...
