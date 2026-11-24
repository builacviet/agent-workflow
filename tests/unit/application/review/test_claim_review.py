from dataclasses import replace
from unittest.mock import Mock

import pytest
from test_review_writes import (
    HEAD,
    TASK,
    claim_checks_ci_and_commits_only_after_validation,
    scope_for,
)

from agent_workflow.application.review.use_cases.claim_review import ClaimReviewUseCase
from agent_workflow.application.review.use_cases.write_contracts import ClaimReviewInput
from agent_workflow.domain.review.policy import ClaimFacts
from agent_workflow.domain.workflow_identity import WorkflowError


def test_claim_validation():
    claim_checks_ci_and_commits_only_after_validation()


@pytest.mark.parametrize(
    ("task", "sha", "claim", "message"),
    [
        (replace(TASK, status="AWAITING_HANDOFF"), HEAD, None, "not reviewable"),
        (replace(TASK, rounds=2), HEAD, None, "not reviewable"),
        (TASK, "c" * 40, None, "stale claim SHA"),
        (
            TASK,
            HEAD,
            ClaimFacts("other", "other", HEAD, 101, "ACTIVE", None),
            "not reviewable",
        ),
    ],
)
def test_claim_rejections_do_not_write(task, sha, claim, message):
    repo = Mock()
    repo.lock_task.return_value = task
    repo.active_claim.return_value = claim
    repo.has_successful_ci.return_value = True
    case = ClaimReviewUseCase(
        scope_for(repo), clock=lambda: 100, token=lambda: "token", require_ci=True
    )
    with pytest.raises(WorkflowError, match=message):
        case.execute(ClaimReviewInput("T", sha, "lead"))
    repo.claim.assert_not_called()
    repo.expire.assert_not_called()


def test_expired_claim_is_recovered_before_reclaim():
    repo = Mock()
    repo.lock_task.return_value = replace(TASK, status="IN_REVIEW")
    old = ClaimFacts("old", "lead", HEAD, 100, "ACTIVE", None)
    repo.active_claim.return_value = old
    repo.has_successful_ci.return_value = True
    case = ClaimReviewUseCase(
        scope_for(repo), clock=lambda: 100, token=lambda: "new", require_ci=True
    )
    result = case.execute(ClaimReviewInput("T", HEAD, "lead"))
    repo.expire.assert_called_once_with(repo.lock_task.return_value, old, 100)
    assert result.claim_token == "new"
