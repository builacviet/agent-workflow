from unittest.mock import Mock, call

import pytest

from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.budget import operation_timeout
from agent_workflow.domain.exceptions import BusinessRuleError

BASE = "a" * 40
HEAD = "b" * 40


@pytest.fixture
def ports():
    effects = Mock()
    effects.leadership.try_acquire.return_value = True
    effects.leadership.is_held.return_value = True
    effects.dispatch.claim.return_value = {
        "dispatch_id": "pass",
        "task_id": "T",
        "input_sha": HEAD,
        "deadline": 9_999_999_999.0,
        "round_number": 1,
    }
    effects.assignment.get_coder_assignment.return_value = {
        "task_id": "T",
        "status": "CHANGES_REQUESTED",
        "completed_review_rounds": 1,
        "base_sha": BASE,
        "current_head_sha": HEAD,
    }
    effects.worker.return_value = HEAD
    return effects


def use_case(ports):
    return RunDispatchPassUseCase(
        ports.leadership,
        ports.dispatch,
        ports.assignment,
        ports.worker,
        on_finished=ports.cleanup,
    )


def test_success_fences_each_effect_and_cleans_up_before_release(ports):
    claim = ports.dispatch.claim.return_value
    assignment = ports.assignment.get_coder_assignment.return_value
    guards = []

    def worker(actual_claim, actual_assignment, guard):
        assert actual_claim is claim
        assert actual_assignment is assignment
        guards.append(guard)
        guard()
        return HEAD

    ports.worker.side_effect = worker
    assert use_case(ports).execute({"T"}, 60)

    owner = call.leadership.is_held()
    assert ports.mock_calls == [
        call.leadership.try_acquire(),
        owner,
        call.dispatch.claim({"T"}, 60, track_pre_launch=True),
        owner,
        owner,
        call.assignment.get_coder_assignment("T"),
        owner,
        owner,
        call.worker(claim, assignment, guards[0]),
        owner,  # The Worker uses its raising guard for its own effects.
        owner,
        owner,
        call.dispatch.finish("pass", HEAD),
        owner,
        owner,
        call.cleanup("pass"),
        owner,
        call.leadership.release(),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("completed_review_rounds", 0),
        ("status", "IN_REVIEW"),
        ("current_head_sha", BASE),
        ("current_head_sha", None),
    ],
)
def test_stale_assignment_blocks_without_worker_or_cleanup(ports, field, value):
    ports.assignment.get_coder_assignment.return_value[field] = value

    with pytest.raises(BusinessRuleError, match="assignment changed"):
        use_case(ports).execute({"T"})

    ports.worker.assert_not_called()
    ports.dispatch.finish.assert_not_called()
    ports.cleanup.assert_not_called()
    ports.dispatch.block.assert_called_once_with("pass")
    ports.leadership.release.assert_called_once()


@pytest.mark.parametrize(
    ("task_ids", "assignment_task_id"),
    [({"t", "T-other"}, "T"), ({"T"}, "OTHER"), ({"T"}, None)],
    ids=["outside-exact-allowlist", "mismatched-assignment", "missing-assignment-task"],
)
def test_ineligible_task_blocks_before_worker_callback(
    ports, task_ids, assignment_task_id
):
    assignment = ports.assignment.get_coder_assignment.return_value
    if assignment_task_id is None:
        del assignment["task_id"]
    else:
        assignment["task_id"] = assignment_task_id

    with pytest.raises(BusinessRuleError, match="dispatch outside exact task allowlist"):
        use_case(ports).execute(task_ids)

    ports.dispatch.claim.assert_called_once_with(task_ids, 1800, track_pre_launch=True)
    ports.assignment.get_coder_assignment.assert_called_once_with("T")
    ports.worker.assert_not_called()
    ports.dispatch.finish.assert_not_called()
    ports.cleanup.assert_not_called()
    ports.dispatch.block.assert_called_once_with("pass")
    ports.leadership.release.assert_called_once()


def test_expired_claim_stops_before_assignment_and_releases_budget(ports):
    ports.dispatch.claim.return_value["deadline"] = 0.0

    with pytest.raises(TimeoutError, match="dispatch deadline exceeded"):
        use_case(ports).execute({"T"})

    ports.assignment.get_coder_assignment.assert_not_called()
    ports.worker.assert_not_called()
    ports.dispatch.finish.assert_not_called()
    ports.cleanup.assert_not_called()
    ports.dispatch.block.assert_called_once_with("pass")
    ports.leadership.release.assert_called_once()
    assert operation_timeout(10.0) == 10.0


@pytest.mark.parametrize("lost_before_block", [True, False])
def test_failed_worker_fences_block_and_releases(ports, lost_before_block):
    def fail(*_):
        ports.leadership.is_held.side_effect = [True, not lost_before_block, False]
        raise RuntimeError("Worker failed")

    ports.worker.side_effect = fail
    with pytest.raises(PermissionError, match="leadership lost"):
        use_case(ports).execute({"T"})

    if lost_before_block:
        ports.dispatch.block.assert_not_called()
    else:
        ports.dispatch.block.assert_called_once_with("pass")
    ports.dispatch.finish.assert_not_called()
    ports.cleanup.assert_not_called()
    ports.leadership.release.assert_called_once()


def test_lost_owner_leaves_active_claim_for_reconciliation(ports):
    def worker(*_):
        ports.leadership.is_held.return_value = False
        return HEAD

    ports.worker.side_effect = worker
    with pytest.raises(PermissionError, match="reconcile in-flight work"):
        use_case(ports).execute({"T"})

    ports.dispatch.block.assert_not_called()
    ports.dispatch.finish.assert_not_called()
    ports.cleanup.assert_not_called()
    ports.leadership.release.assert_called_once()

    # A fresh owner sees no available claim while the ledger retains the slot.
    successor = Mock()
    successor.try_acquire.return_value = True
    successor.is_held.return_value = True
    ports.dispatch.claim.return_value = None
    assert not RunDispatchPassUseCase(
        successor,
        ports.dispatch,
        ports.assignment,
        ports.worker,
        on_finished=ports.cleanup,
    ).execute({"T"})
    successor.release.assert_called_once()
    ports.worker.assert_called_once()
    ports.dispatch.block.assert_not_called()
    ports.dispatch.finish.assert_not_called()
    ports.cleanup.assert_not_called()


@pytest.mark.parametrize("lost_before_cleanup", [True, False])
def test_cleanup_fence_never_rewrites_committed_finish(ports, lost_before_cleanup):
    def finish(*_):
        ports.leadership.is_held.side_effect = [True, not lost_before_cleanup, False]

    ports.dispatch.finish.side_effect = finish
    with pytest.raises(BusinessRuleError, match="reconcile engine pin"):
        use_case(ports).execute({"T"})

    ports.dispatch.finish.assert_called_once_with("pass", HEAD)
    if lost_before_cleanup:
        ports.cleanup.assert_not_called()
    else:
        ports.cleanup.assert_called_once_with("pass")
    ports.dispatch.block.assert_not_called()
    ports.leadership.release.assert_called_once()
