from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest
from test_review_writes import HEAD, TASK

from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.review.use_cases.claim_review import ClaimReviewUseCase
from agent_workflow.application.review.use_cases.write_contracts import ClaimReviewInput
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.review.policy import ClaimFacts


@pytest.mark.parametrize(
    ("request_data", "error_type", "message"),
    [
        (
            ClaimReviewInput(" ", HEAD, "lead"),
            InvalidApplicationRequestError,
            "unknown authorized task",
        ),
        (
            ClaimReviewInput("T", HEAD, " "),
            InvalidApplicationRequestError,
            "invalid reviewer",
        ),
        (ClaimReviewInput("T", "short", "lead"), BusinessRuleError, "full lowercase"),
    ],
)
def test_invalid_claim_does_not_open_uow(request_data, error_type, message):
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    case = ClaimReviewUseCase(uow, repo, clock=Mock(), token=Mock(), require_ci=True)
    with pytest.raises(error_type, match=message):
        case.execute(request_data)
    uow.__enter__.assert_not_called()
    uow.commit.assert_not_called()
    assert repo.mock_calls == []


@pytest.mark.parametrize(
    ("task", "sha", "claim", "message"),
    [
        (None, HEAD, None, "unknown authorized task"),
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
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    repo.lock_task.return_value = task
    repo.active_claim.return_value = claim
    repo.has_successful_ci.return_value = True
    case = ClaimReviewUseCase(
        uow, repo, clock=lambda: 100, token=lambda: "token", require_ci=True
    )
    with pytest.raises(BusinessRuleError, match=message):
        case.execute(ClaimReviewInput("T", sha, "lead"))
    repo.claim.assert_not_called()
    repo.expire.assert_not_called()
    uow.commit.assert_not_called()


def test_expired_claim_is_recovered_before_reclaim():
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    repo.lock_task.return_value = replace(TASK, status="IN_REVIEW")
    old = ClaimFacts("old", "lead", HEAD, 100, "ACTIVE", None)
    repo.active_claim.return_value = old
    repo.has_successful_ci.return_value = True
    case = ClaimReviewUseCase(
        uow, repo, clock=lambda: 100, token=lambda: "new", require_ci=True
    )
    result = case.execute(ClaimReviewInput("T", HEAD, "lead"))
    repo.expire.assert_called_once_with(repo.lock_task.return_value, old, 100)
    assert result.claim_token == "new"
    uow.commit.assert_called_once_with()
