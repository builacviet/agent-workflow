"""Strict public data contracts. Model names are intentionally absent."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Finding(StrictModel):
    finding_id: StrictStr = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    acceptance_criterion: StrictStr = Field(min_length=1)
    file_path: StrictStr = Field(min_length=1)
    line: int | None = Field(default=None, ge=1)
    description: StrictStr = Field(min_length=1)
    suggested_correction: StrictStr = Field(min_length=1)
    required_verification: StrictStr = Field(min_length=1)


Resolution = Literal["FIXED", "NOT_FIXED", "PARTIALLY_FIXED", "NOT_VERIFIED"]


class Submission(StrictModel):
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


class TaskSpec(StrictModel):
    task_id: StrictStr = Field(min_length=1)
    title: StrictStr = Field(min_length=1)
    description: StrictStr = Field(min_length=1)
    acceptance_criteria: list[StrictStr] = Field(min_length=1)
    definition_of_done: StrictStr = Field(min_length=1)
    repository: StrictStr
    branch: StrictStr = Field(min_length=1)
    base_sha: StrictStr
    requested_effort: StrictStr
    risk_level: StrictStr
    require_verified_effort: bool = True
    context_paths: list[StrictStr] = Field(min_length=1)
    synthetic: bool = False
