from unittest.mock import Mock

import pytest
from test_review_writes import (
    HEAD,
    TASK,
    scope_for,
    submission,
    submit_rejects_expired_and_conflicting_duplicate_without_write,
)

from agent_workflow.application.review.use_cases.submit_review import (
    SubmitReviewUseCase,
)
from agent_workflow.application.review.use_cases.write_contracts import (
    SubmitReviewInput,
)
from agent_workflow.domain.review.policy import ClaimFacts
from agent_workflow.domain.workflow_identity import WorkflowError


def test_submission_validation():
    submit_rejects_expired_and_conflicting_duplicate_without_write()


def test_submit_duplicate_and_effort_gate():
    import json
    from dataclasses import replace

    repo = Mock()
    task = replace(TASK, status="IN_REVIEW", verified_effort=True)
    repo.lock_task.return_value = task
    repo.has_successful_ci.return_value = True
    repo.prior_findings.return_value = set()
    repo.find_claim.return_value = ClaimFacts(
        "token", "lead", HEAD, 101, "ACTIVE", None
    )
    case = SubmitReviewUseCase(
        scope_for(repo), clock=lambda: 100, token=lambda: "review", require_ci=True
    )
    with pytest.raises(WorkflowError, match="effort cannot be verified"):
        case.execute(SubmitReviewInput(submission(), "lead"))
    repo.submit.assert_not_called()
    verified = SubmitReviewUseCase(
        scope_for(repo),
        clock=lambda: 100,
        token=lambda: "review",
        require_ci=True,
        effort_verifier=lambda *_: ("medium", "verified"),
    )
    result = verified.execute(SubmitReviewInput(submission(), "lead"))
    assert result.actual_effort == "medium"
    assert repo.submit.call_count == 1
    canonical = json.dumps(
        submission().model_dump(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    repo.find_claim.return_value = ClaimFacts(
        "token", "lead", HEAD, 101, "SUBMITTED", canonical
    )
    assert case.execute(SubmitReviewInput(submission(), "lead")).duplicate
    assert repo.submit.call_count == 1
