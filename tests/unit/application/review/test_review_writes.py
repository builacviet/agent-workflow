from unittest.mock import MagicMock, Mock, call

import pytest

from agent_workflow.application.review.use_cases.claim_review import ClaimReviewUseCase
from agent_workflow.application.review.use_cases.submit_review import (
    SubmitReviewUseCase,
)
from agent_workflow.application.review.use_cases.write_contracts import (
    ClaimReviewInput,
    ReviewSubmission,
    SubmitReviewInput,
)
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.review.policy import (
    ClaimFacts,
    TaskFacts,
    require_ci,
    verdict_allowed,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError

HEAD = "a" * 40
TASK = TaskFacts("T", "READY_IN_REVIEW", HEAD, "b" * 40, 0, "medium", False)


def submission(**updates):
    fields = {
        "task_id": "T",
        "claim_token": "token",
        "reviewed_head_sha": HEAD,
        "review_round": 1,
        "verdict": WorkflowStatus.APPROVED,
        "summary": "ok",
        "findings": (),
        "prior_resolutions": {},
        "requested_effort": "medium",
    }
    return ReviewSubmission(**(fields | updates))


def test_claim_checks_ci_and_commits_only_after_validation():
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    repo.lock_task.return_value = TASK
    repo.active_claim.return_value = None
    repo.has_successful_ci.return_value = False
    calls = Mock()
    calls.attach_mock(repo, "repo")
    calls.attach_mock(uow, "uow")
    case = ClaimReviewUseCase(
        uow, repo, clock=lambda: 100, token=lambda: "token", require_ci=True
    )
    with pytest.raises(WorkflowError, match="exact-HEAD CI"):
        case.execute(ClaimReviewInput("T", HEAD, "lead"))
    repo.claim.assert_not_called()
    uow.commit.assert_not_called()
    calls.reset_mock()
    repo.has_successful_ci.return_value = True
    result = case.execute(ClaimReviewInput("T", HEAD, "lead"))
    assert result.claim_token == "token" and result.lease_expiration == 1000
    repo.claim.assert_called_once()
    assert calls.mock_calls == [
        call.uow.__enter__(),
        call.repo.lock_task("T"),
        call.repo.active_claim("T"),
        call.repo.has_successful_ci("T", HEAD),
        call.repo.claim(
            TASK, ClaimFacts("token", "lead", HEAD, 1000, "ACTIVE", None), 100
        ),
        call.uow.commit(),
        call.uow.__exit__(None, None, None),
    ]


def test_submit_rejects_expired_and_conflicting_duplicate_without_write():
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    repo.lock_task.return_value = TaskFacts(
        "T", "IN_REVIEW", HEAD, TASK.base, 0, "medium", False
    )
    repo.has_successful_ci.return_value = True
    repo.find_claim.return_value = ClaimFacts(
        "token", "lead", HEAD, 100, "ACTIVE", None
    )
    case = SubmitReviewUseCase(
        uow, repo, clock=lambda: 100, token=lambda: "review", require_ci=True
    )
    with pytest.raises(WorkflowError, match="claim expired"):
        case.execute(SubmitReviewInput(submission(), "lead"))
    repo.find_claim.return_value = ClaimFacts(
        "token", "lead", HEAD, 100, "SUBMITTED", "different"
    )
    with pytest.raises(WorkflowError, match="conflicting duplicate"):
        case.execute(SubmitReviewInput(submission(), "lead"))
    repo.submit.assert_not_called()
    uow.commit.assert_not_called()


def test_round_policy_and_repair_ci():
    assert not require_ci(False, "CI_REPAIR_HANDOFF", False)
    assert require_ci(False, "HANDOFF", False)
    verdict_allowed(2, "APPROVED", {"F"}, {"F": "FIXED"}, [])
    with pytest.raises(WorkflowError, match="two-round"):
        verdict_allowed(2, "CHANGES_REQUESTED", set(), {}, ["F"])
    with pytest.raises(WorkflowError, match="explicit resolution"):
        verdict_allowed(2, "APPROVED", {"F"}, {}, [])
    with pytest.raises(WorkflowError, match="unique"):
        verdict_allowed(2, "ESCALATED", {"F"}, {"F": "FIXED"}, ["F"])
