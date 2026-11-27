import pytest

from agent_workflow.domain.dispatch.policy import validate_dispatch_eligibility
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.states import WorkflowStatus

BASE = "a" * 40
HEAD = "b" * 40


@pytest.mark.parametrize(
    "status", [WorkflowStatus.AWAITING_HANDOFF, WorkflowStatus.CHANGES_REQUESTED]
)
@pytest.mark.parametrize("head", [None, "", HEAD])
def test_accepts_eligible_status_with_effective_input(status, head):
    assert (
        validate_dispatch_eligibility(
            status=status,
            completed_review_rounds=1,
            claimed_round=1,
            current_head_sha=head,
            base_sha=BASE,
            claimed_input_sha=head or BASE,
        )
        is None
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"completed_review_rounds": 0},
        {"status": WorkflowStatus.IN_REVIEW},
        {"claimed_input_sha": BASE},
        {"current_head_sha": None},
        {"completed_review_rounds": 0, "status": []},
    ],
)
def test_rejects_changed_assignment(changes):
    values = dict(
        status=WorkflowStatus.CHANGES_REQUESTED,
        completed_review_rounds=1,
        claimed_round=1,
        current_head_sha=HEAD,
        base_sha=BASE,
        claimed_input_sha=HEAD,
    )
    with pytest.raises(BusinessRuleError, match="^assignment changed after dispatch$"):
        validate_dispatch_eligibility(**(values | changes))
