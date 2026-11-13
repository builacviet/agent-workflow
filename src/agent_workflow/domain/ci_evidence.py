"""Shared contracts for trusted CI evidence and signed inbox notifications."""

import re
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from agent_workflow.domain.states import CiVerdict
from agent_workflow.domain.workflow_identity import WorkflowError

Verdict = Literal["SUCCESS", "FAILURE", "BLOCKED"]


@dataclass(frozen=True, slots=True)
class CiEvidence:
    task_id: str
    repository: str
    installation_id: int
    pr_number: int
    expected_head_sha: str
    observed_head_sha: str | None
    tested_sha: str | None
    check_name: str
    check_app_slug: str
    verdict: Verdict
    reason: str
    check_run_id: int | None
    workflow_run_id: int | None
    run_attempt: int | None
    completed_at: str | None
    observed_at: str

    def as_dict(self) -> dict:
        """Serialize the immutable observation, never an authorization token."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WebhookNotification:
    delivery_id: str
    event: str
    action: str
    repository: str
    installation_id: int
    subject_id: int
    head_sha: str


class ControllerCiStore(Protocol):
    """Persistence contract for the trusted Controller's CI state."""

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None: ...

    def tracked(self, limit: int = 32) -> list[dict]: ...

    def previous_failure(self, task_id: str, head_sha: str) -> bool: ...

    def record(self, evidence: CiEvidence) -> None: ...

    def approved(self, task_id: str, head_sha: str) -> bool: ...

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]: ...

    def mark_processed(self, delivery_id: str) -> None: ...


def validate_evidence(evidence: CiEvidence) -> None:
    """Validate the durable ledger's trust and exact-HEAD requirements."""
    if type(evidence) is not CiEvidence:
        raise WorkflowError("untrusted CI evidence type")
    if (
        evidence.check_name != "Python tests"
        or evidence.check_app_slug != "github-actions"
        or type(evidence.installation_id) is not int
        or evidence.installation_id <= 0
    ):
        raise WorkflowError("CI evidence source is not authorized")
    if evidence.verdict not in tuple(CiVerdict):
        raise WorkflowError("invalid CI verdict")
    terminal_reasons = {
        CiVerdict.SUCCESS: "REQUIRED_CHECK_PASSED",
        CiVerdict.FAILURE: "REQUIRED_CHECK_FAILED",
    }
    if evidence.verdict in terminal_reasons and (
        evidence.reason != terminal_reasons[evidence.verdict]
        or evidence.observed_head_sha != evidence.expected_head_sha
        or evidence.tested_sha != evidence.expected_head_sha
        or type(evidence.check_run_id) is not int
        or evidence.check_run_id <= 0
        or type(evidence.workflow_run_id) is not int
        or evidence.workflow_run_id <= 0
        or type(evidence.run_attempt) is not int
        or evidence.run_attempt <= 0
        or not isinstance(evidence.completed_at, str)
        or not evidence.completed_at
    ):
        raise WorkflowError(
            f"CI {evidence.verdict.lower()} lacks exact-HEAD trusted evidence"
        )


def validate_delivery_id(delivery_id: str) -> None:
    if not isinstance(delivery_id, str) or not re.fullmatch(
        r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", delivery_id
    ):
        raise WorkflowError("invalid webhook delivery identity")
