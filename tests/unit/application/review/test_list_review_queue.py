from unittest.mock import MagicMock, Mock, call

import pytest

from agent_workflow.application.review.dtos import (
    ActiveReviewClaim,
    ClaimedReviewTask,
    ReviewLeaseTransition,
    ReviewQueueItem,
)
from agent_workflow.application.review.ports import (
    IReviewLeaseStore,
    IReviewQueueReader,
)
from agent_workflow.application.review.use_cases.list_review_queue import (
    ListReviewQueueUseCase,
)
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError

HEAD = "a" * 40
ITEM = ReviewQueueItem("T", "Task", "owner/repo", "branch", HEAD, "high", 1)


@pytest.fixture
def review_queue():
    calls = Mock()
    calls.attach_mock(MagicMock(spec=IUnitOfWork), "uow")
    calls.attach_mock(Mock(spec=IReviewLeaseStore), "leases")
    calls.attach_mock(Mock(spec=IReviewQueueReader), "reader")
    calls.leases.lock_claimed_tasks.return_value = ()
    calls.reader.read.return_value = (ITEM,)

    return calls


def test_expiry_includes_clock_boundary_and_uses_same_scope_as_queue_read(review_queue):
    calls = review_queue
    expired = ClaimedReviewTask("T", HEAD, WorkflowStatus.IN_REVIEW)
    active = ClaimedReviewTask("U", HEAD, WorkflowStatus.IN_REVIEW)
    no_claim = ClaimedReviewTask("V", HEAD, WorkflowStatus.IN_REVIEW)
    calls.leases.lock_claimed_tasks.return_value = (expired, active, no_claim)
    calls.leases.find_active_claim.side_effect = [
        ActiveReviewClaim("expired-token", 1000.0),
        ActiveReviewClaim("active-token", 1000.1),
        None,
    ]
    calls.reader.has_successful_ci.return_value = True
    clock = Mock(side_effect=[1000.0, 1000.05, 1000.06])
    use_case = ListReviewQueueUseCase(
        calls.uow, calls.leases, calls.reader, require_ci=True, clock=clock
    )

    assert use_case.execute("high") == (ITEM,)
    assert calls.mock_calls == [
        call.uow.__enter__(),
        call.leases.lock_claimed_tasks(),
        call.leases.find_active_claim("T"),
        call.leases.expire_claim("expired-token"),
        call.leases.record_transition(
            ReviewLeaseTransition(
                expired, WorkflowStatus.READY_IN_REVIEW, "LEASE_EXPIRED", 1000.05
            )
        ),
        call.leases.find_active_claim("U"),
        call.leases.find_active_claim("V"),
        call.reader.read("high"),
        call.reader.has_successful_ci("T", HEAD),
        call.uow.commit(),
        call.uow.__exit__(None, None, None),
    ]
    assert clock.call_count == 3


@pytest.mark.parametrize(
    ("require_ci", "handoff_action", "successful_ci", "allowed"),
    [
        (True, None, False, False),
        (True, None, True, True),
        (False, None, False, True),
        (False, "HANDOFF", False, True),
        (False, "CI_REPAIR_HANDOFF", False, False),
        (False, "CI_REPAIR_HANDOFF", True, True),
    ],
)
def test_ci_policy_checks_exact_head_for_required_and_repair_handoffs(
    review_queue, require_ci, handoff_action, successful_ci, allowed
):
    calls = review_queue
    calls.reader.latest_head_handoff_action.return_value = handoff_action
    calls.reader.has_successful_ci.return_value = successful_ci

    result = ListReviewQueueUseCase(
        calls.uow, calls.leases, calls.reader, require_ci=require_ci, clock=Mock()
    ).execute()

    assert result == ((ITEM,) if allowed else ())
    calls.uow.commit.assert_not_called()
    if require_ci:
        calls.reader.latest_head_handoff_action.assert_not_called()
    else:
        calls.reader.latest_head_handoff_action.assert_called_once_with("T", HEAD)
    if require_ci or handoff_action == "CI_REPAIR_HANDOFF":
        calls.reader.has_successful_ci.assert_called_once_with("T", HEAD)
    else:
        calls.reader.has_successful_ci.assert_not_called()


def test_empty_queue_with_nonexpired_claim_does_not_commit(review_queue):
    calls = review_queue
    calls.leases.lock_claimed_tasks.return_value = (
        ClaimedReviewTask("T", HEAD, WorkflowStatus.IN_REVIEW),
    )
    calls.leases.find_active_claim.return_value = ActiveReviewClaim("active", 1001.0)
    calls.reader.read.return_value = ()
    use_case = ListReviewQueueUseCase(
        calls.uow, calls.leases, calls.reader, require_ci=True, clock=lambda: 1000.0
    )

    assert use_case.execute() == ()
    calls.leases.expire_claim.assert_not_called()
    calls.leases.record_transition.assert_not_called()
    calls.uow.commit.assert_not_called()


def test_selection_failure_escapes_uow_without_commit_after_recovery(review_queue):
    calls = review_queue
    calls.leases.lock_claimed_tasks.return_value = (
        ClaimedReviewTask("T", HEAD, WorkflowStatus.IN_REVIEW),
    )
    calls.leases.find_active_claim.return_value = ActiveReviewClaim("expired", 1000.0)
    failure = RuntimeError("selection failed")
    calls.reader.has_successful_ci.side_effect = failure
    use_case = ListReviewQueueUseCase(
        calls.uow, calls.leases, calls.reader, require_ci=True, clock=lambda: 1000.0
    )

    with pytest.raises(RuntimeError, match="selection failed"):
        use_case.execute()
    calls.leases.expire_claim.assert_called_once_with("expired")
    calls.leases.record_transition.assert_called_once()
    calls.uow.commit.assert_not_called()
    assert calls.uow.__exit__.call_args.args[:2] == (RuntimeError, failure)


def test_invalid_effort_type_does_not_open_uow(review_queue):
    calls = review_queue
    clock = Mock()
    use_case = ListReviewQueueUseCase(
        calls.uow, calls.leases, calls.reader, require_ci=True, clock=clock
    )
    with pytest.raises(WorkflowError, match="requested_effort must be a string"):
        use_case.execute(1)
    assert calls.mock_calls == []
    clock.assert_not_called()
