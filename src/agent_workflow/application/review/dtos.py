"""Layer: Application
Responsibility: Define immutable review context, queue and lease data.
Dependencies: Domain workflow statuses.
"""

from dataclasses import dataclass

from agent_workflow.domain.states import WorkflowStatus


@dataclass(frozen=True)
class ReviewTask:
    task_id: str
    repository: str
    branch: str
    base_sha: str
    current_head_sha: str | None
    requested_effort: str
    require_verified_effort: int
    max_review_rounds: int
    completed_review_rounds: int
    status: WorkflowStatus
    created_at: float
    updated_at: float
    context_paths: tuple[str, ...]
    spec_json: str


@dataclass(frozen=True)
class ReviewFinding:
    review_id: str
    finding_id: str
    task_id: str
    severity: str
    acceptance_criterion: str
    file_path: str
    line: int | None
    description: str
    suggested_correction: str
    required_verification: str


@dataclass(frozen=True)
class PreviousReview:
    review_id: str
    task_id: str
    round_number: int
    base_sha: str
    reviewed_head_sha: str
    verdict: WorkflowStatus
    summary: str
    requested_effort: str
    actual_effort: str | None
    verification_evidence: str
    reviewer: str | None
    reviewed_at: float | None
    imported_at: float | None
    prior_resolutions: tuple[tuple[str, str], ...]
    findings: tuple[ReviewFinding, ...]


@dataclass(frozen=True)
class ReviewEvent:
    event_id: int
    task_id: str
    previous_status: WorkflowStatus | None
    next_status: WorkflowStatus
    action: str
    sha: str | None
    timestamp: float
    metadata: str


@dataclass(frozen=True)
class ReviewContextSnapshot:
    task: ReviewTask
    previous_reviews: tuple[PreviousReview, ...]
    events: tuple[ReviewEvent, ...]


@dataclass(frozen=True)
class ReviewQueueItem:
    task_id: str
    title: str
    repository: str
    branch: str
    head_sha: str | None
    requested_effort: str
    review_round: int


@dataclass(frozen=True)
class ClaimedReviewTask:
    task_id: str
    head_sha: str | None
    status: WorkflowStatus


@dataclass(frozen=True)
class ActiveReviewClaim:
    token: str
    expires_at: float


@dataclass(frozen=True)
class ReviewLeaseTransition:
    task: ClaimedReviewTask
    next_status: WorkflowStatus
    action: str
    timestamp: float
