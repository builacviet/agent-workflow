"""Layer: Application
Responsibility: Define transactional review write contracts.
Dependencies: Domain review facts and submission contract.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from agent_workflow.domain.contracts import Submission
from agent_workflow.domain.review.policy import ClaimFacts, TaskFacts, require_ci


@dataclass(frozen=True)
class ClaimReviewInput:
    task_id: str
    expected_head_sha: str
    reviewer: str


@dataclass(frozen=True)
class ClaimReviewOutput:
    claim_token: str
    task_id: str
    reviewed_sha: str
    review_round: int
    lease_expiration: float


@dataclass(frozen=True)
class SubmitReviewInput:
    submission: Submission
    reviewer: str


@dataclass(frozen=True)
class SubmitReviewOutput:
    task_id: str
    verdict: str
    reviewed_head_sha: str
    duplicate: bool = False
    review_id: str | None = None
    review_round: int | None = None
    actual_effort: str | None = None


class IReviewWriteRepository(Protocol):
    def lock_task(self, task_id: str) -> TaskFacts | None: ...
    def active_claim(self, task_id: str) -> ClaimFacts | None: ...
    def find_claim(self, task_id: str, token: str) -> ClaimFacts | None: ...
    def latest_head_handoff_action(self, task_id: str, sha: str) -> str | None: ...
    def has_successful_ci(self, task_id: str, sha: str) -> bool: ...
    def prior_findings(self, task_id: str) -> set[str]: ...
    def expire(self, task: TaskFacts, claim: ClaimFacts, now: float) -> None: ...
    def claim(self, task: TaskFacts, claim: ClaimFacts, now: float) -> None: ...
    def submit(
        self,
        task: TaskFacts,
        submission: Submission,
        canonical: str,
        review_id: str,
        reviewer: str,
        now: float,
        actual: str | None,
        evidence: str,
    ) -> None: ...


class IReviewWriteScope(Protocol):
    def __call__(self) -> AbstractContextManager[IReviewWriteRepository]: ...


def ci_allows(
    repo: IReviewWriteRepository, task_id: str, sha: str, required: bool
) -> bool:
    handoff = None if required else repo.latest_head_handoff_action(task_id, sha)
    if require_ci(required, handoff, False):
        return True
    return require_ci(required, handoff, repo.has_successful_ci(task_id, sha))
