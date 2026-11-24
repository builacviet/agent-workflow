from contextlib import contextmanager
from unittest.mock import Mock, call

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
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError

HEAD = "a" * 40
ITEM = ReviewQueueItem("T", "Task", "owner/repo", "branch", HEAD, "high", 1)


@pytest.fixture
def review_queue():
    calls = Mock()
    calls.attach_mock(Mock(spec=IReviewLeaseStore), "leases")
    calls.attach_mock(Mock(spec=IReviewQueueReader), "reader")
    calls.leases.lock_claimed_tasks.return_value = ()
    calls.reader.read.return_value = (ITEM,)

    @contextmanager
    def scope():
        calls.enter()
        try:
            yield calls.leases, calls.reader
        finally:
            calls.exit()

    return calls, scope


def test_expiry_includes_clock_boundary_and_uses_same_scope_as_queue_read(review_queue):
    calls, scope = review_queue
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
    use_case = ListReviewQueueUseCase(scope, require_ci=True, clock=clock)

    assert use_case.execute("high") == (ITEM,)
    assert calls.mock_calls == [
        call.enter(),
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
        call.exit(),
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
    calls, scope = review_queue
    calls.reader.latest_head_handoff_action.return_value = handoff_action
    calls.reader.has_successful_ci.return_value = successful_ci

    result = ListReviewQueueUseCase(
        scope, require_ci=require_ci, clock=Mock()
    ).execute()

    assert result == ((ITEM,) if allowed else ())
    if require_ci:
        calls.reader.latest_head_handoff_action.assert_not_called()
    else:
        calls.reader.latest_head_handoff_action.assert_called_once_with("T", HEAD)
    if require_ci or handoff_action == "CI_REPAIR_HANDOFF":
        calls.reader.has_successful_ci.assert_called_once_with("T", HEAD)
    else:
        calls.reader.has_successful_ci.assert_not_called()


def test_invalid_effort_type_does_not_open_scope():
    scope, clock = Mock(), Mock()
    with pytest.raises(WorkflowError, match="requested_effort must be a string"):
        ListReviewQueueUseCase(scope, require_ci=True, clock=clock).execute(1)
    scope.assert_not_called()
    clock.assert_not_called()
