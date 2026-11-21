"""CI tracking, recording and approval rules; no persistence dependencies."""

from dataclasses import dataclass

from agent_workflow.domain.ci_evidence import CiEvidence
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.workflow_identity import REPOSITORY


@dataclass(frozen=True, slots=True)
class CiTrackingSnapshot:
    """Task authorization plus its optional durable CI binding."""

    status: str | None
    repository: str | None
    current_head_sha: str | None
    pr_number: int | None
    binding_head_sha: str | None

    def needs_binding(self, pr_number: int, head_sha: str) -> bool:
        if (
            self.status != WorkflowStatus.READY_IN_REVIEW
            or self.current_head_sha != head_sha
            or self.repository != REPOSITORY
        ):
            raise BusinessRuleError("CI tracking requires authorized exact task HEAD")
        if self.pr_number is None:
            return True
        if self.pr_number != pr_number:
            raise BusinessRuleError("CI tracking PR differs from persisted binding")
        if self.binding_head_sha != head_sha:
            raise BusinessRuleError("CI tracking HEAD differs from persisted binding")
        return False


@dataclass(frozen=True, slots=True)
class CiRecordSnapshot:
    """Authorized Task/binding and complete same-HEAD failure history."""

    repository: str | None
    current_head_sha: str | None
    pr_number: int | None
    binding_head_sha: str | None
    had_failure: bool

    def verdict_for(self, evidence: CiEvidence) -> str:
        if (
            self.repository != REPOSITORY
            or evidence.repository != self.repository
            or self.current_head_sha != evidence.expected_head_sha
            or self.pr_number != evidence.pr_number
            or self.binding_head_sha != evidence.expected_head_sha
        ):
            raise BusinessRuleError("CI evidence mismatched task/PR/HEAD")
        return CiVerdict.FAILURE if self.had_failure else evidence.verdict


@dataclass(frozen=True, slots=True)
class CiApprovalSnapshot:
    """Projection assembled by the Reader, never a mutable ORM entity."""

    binding_head_sha: str
    verdict: str
    task_head_sha: str
    repository: str
    verified_success: bool

    def approves(self, head_sha: str) -> bool:
        return (
            self.binding_head_sha == head_sha
            and self.verdict == CiVerdict.SUCCESS
            and self.task_head_sha == self.binding_head_sha
            and self.repository == REPOSITORY
            and self.verified_success
        )
