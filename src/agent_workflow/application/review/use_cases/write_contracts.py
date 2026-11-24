"""Layer: Application
Responsibility: Define transactional review write contracts.
Dependencies: Domain review facts, workflow states and validation.
"""

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol, get_args

from agent_workflow.domain.review.policy import ClaimFacts, TaskFacts, require_ci
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError, validate_sha


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


FindingSeverity = Literal["low", "medium", "high", "critical"]
Resolution = Literal["FIXED", "NOT_FIXED", "PARTIALLY_FIXED", "NOT_VERIFIED"]

_REVIEW_VERDICTS = (
    WorkflowStatus.APPROVED,
    WorkflowStatus.CHANGES_REQUESTED,
    WorkflowStatus.ESCALATED,
)


def _require_text(field: str, value: object, *, nonempty: bool = False) -> None:
    if not isinstance(value, str) or (nonempty and not value):
        requirement = "a nonempty string" if nonempty else "a string"
        raise WorkflowError(f"{field} must be {requirement}")


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    severity: FindingSeverity
    acceptance_criterion: str
    file_path: str
    description: str
    suggested_correction: str
    required_verification: str
    line: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "finding_id",
            "acceptance_criterion",
            "file_path",
            "description",
            "suggested_correction",
            "required_verification",
        ):
            _require_text(name, getattr(self, name), nonempty=True)
        if not isinstance(self.severity, str) or self.severity not in get_args(
            FindingSeverity
        ):
            raise WorkflowError("invalid finding severity")
        if self.line is not None and (type(self.line) is not int or self.line < 1):
            raise WorkflowError("finding line must be a positive integer")


@dataclass(frozen=True)
class ReviewSubmission:
    task_id: str
    claim_token: str
    reviewed_head_sha: str
    review_round: int
    verdict: WorkflowStatus
    summary: str
    findings: tuple[ReviewFinding, ...]
    prior_resolutions: Mapping[str, Resolution]
    requested_effort: str
    actual_effort: str | None = None
    verification_evidence: str | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "claim_token", "reviewed_head_sha", "requested_effort"):
            _require_text(name, getattr(self, name))
        _require_text("summary", self.summary, nonempty=True)
        for name in ("actual_effort", "verification_evidence"):
            value = getattr(self, name)
            if value is not None:
                _require_text(name, value)
        if type(self.review_round) is not int or not 1 <= self.review_round <= 2:
            raise WorkflowError("review_round must be 1 or 2")
        if (
            not isinstance(self.verdict, WorkflowStatus)
            or self.verdict not in _REVIEW_VERDICTS
        ):
            raise WorkflowError("invalid review verdict")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(finding, ReviewFinding) for finding in self.findings
        ):
            raise WorkflowError("findings must be a tuple of review findings")
        if not isinstance(self.prior_resolutions, Mapping):
            raise WorkflowError("prior_resolutions must be a mapping")
        resolutions = dict(self.prior_resolutions)
        allowed = get_args(Resolution)
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or value not in allowed
            for key, value in resolutions.items()
        ):
            raise WorkflowError("invalid prior resolution")
        object.__setattr__(self, "prior_resolutions", MappingProxyType(resolutions))


@dataclass(frozen=True)
class SubmitReviewInput:
    submission: ReviewSubmission
    reviewer: str

    def __post_init__(self) -> None:
        if not isinstance(self.submission, ReviewSubmission):
            raise WorkflowError("invalid review submission")
        if (
            not self.submission.task_id.strip()
            or not self.submission.claim_token.strip()
        ):
            raise WorkflowError("unknown claim or claim owner mismatch")
        if not isinstance(self.reviewer, str) or not self.reviewer.strip():
            raise WorkflowError("invalid reviewer")
        validate_sha(self.submission.reviewed_head_sha)


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
        submission: ReviewSubmission,
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
