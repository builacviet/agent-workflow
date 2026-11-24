"""Layer: Interfaces/MCP
Responsibility: Validate the public review submission and map it to an Application input.
Dependencies: Pydantic, Application review inputs and Domain workflow states.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from agent_workflow.application.review.use_cases.write_contracts import (
    FindingSeverity,
    Resolution,
    ReviewFinding,
    ReviewSubmission,
    SubmitReviewInput,
)
from agent_workflow.domain.states import WorkflowStatus


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Finding(_StrictModel):
    finding_id: StrictStr = Field(min_length=1)
    severity: FindingSeverity
    acceptance_criterion: StrictStr = Field(min_length=1)
    file_path: StrictStr = Field(min_length=1)
    line: int | None = Field(default=None, ge=1)
    description: StrictStr = Field(min_length=1)
    suggested_correction: StrictStr = Field(min_length=1)
    required_verification: StrictStr = Field(min_length=1)

    def to_finding(self) -> ReviewFinding:
        return ReviewFinding(
            finding_id=self.finding_id,
            severity=self.severity,
            acceptance_criterion=self.acceptance_criterion,
            file_path=self.file_path,
            line=self.line,
            description=self.description,
            suggested_correction=self.suggested_correction,
            required_verification=self.required_verification,
        )


class Submission(_StrictModel):
    task_id: StrictStr
    claim_token: StrictStr
    reviewed_head_sha: StrictStr
    review_round: int = Field(ge=1, le=2)
    verdict: Literal["APPROVED", "CHANGES_REQUESTED", "ESCALATED"]
    summary: StrictStr = Field(min_length=1)
    findings: list[Finding]
    prior_resolutions: dict[str, Resolution]
    requested_effort: StrictStr
    actual_effort: StrictStr | None = None
    verification_evidence: StrictStr | None = None

    def to_input(self, reviewer: str) -> SubmitReviewInput:
        return SubmitReviewInput(
            ReviewSubmission(
                task_id=self.task_id,
                claim_token=self.claim_token,
                reviewed_head_sha=self.reviewed_head_sha,
                review_round=self.review_round,
                verdict=WorkflowStatus(self.verdict),
                summary=self.summary,
                findings=tuple(finding.to_finding() for finding in self.findings),
                prior_resolutions=self.prior_resolutions,
                requested_effort=self.requested_effort,
                actual_effort=self.actual_effort,
                verification_evidence=self.verification_evidence,
            ),
            reviewer,
        )
