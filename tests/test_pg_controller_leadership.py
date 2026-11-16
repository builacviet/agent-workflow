"""Deterministic guarded PostgreSQL Controller lifecycle tests."""

from unittest.mock import Mock

import pytest

from agent_workflow.composition.postgres_controller import (
    PostgresControllerDependencies,
)
from agent_workflow.composition.postgres_leader_runner import run_controller_once

SHA = "a" * 40
CLAIM = {
    "dispatch_id": "dispatch",
    "task_id": "T",
    "round_number": 0,
    "input_sha": SHA,
    "deadline": 9999999999.0,
}
ASSIGNMENT = {
    "completed_review_rounds": 0,
    "status": "AWAITING_HANDOFF",
    "current_head_sha": None,
    "base_sha": SHA,
}


class Leader:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.held = acquired
        self.acquire_calls = 0
        self.release_calls = 0

    def try_acquire(self):
        self.acquire_calls += 1
        return self.acquired and self.release_calls == 0

    def is_held(self):
        return self.held

    def release(self):
        self.release_calls += 1
        self.held = False


def fixture(acquired=True):
    leader = Leader(acquired)
    dispatch = Mock()
    dispatch.claim.return_value = CLAIM.copy()
    assignment = Mock()
    assignment.repository = deps_workflow = Mock()
    assignment.get_coder_assignment.return_value = ASSIGNMENT.copy()
    execute = Mock(return_value=SHA)
    deps = PostgresControllerDependencies(deps_workflow, dispatch, Mock(), leader)
    return deps, assignment, execute


def test_denied_and_acquisition_error_do_no_work_and_release():
    deps, assignment, execute = fixture(False)
    assert not run_controller_once(deps, assignment, {"T"}, execute)
    deps.dispatch.claim.assert_not_called()
    assignment.get_coder_assignment.assert_not_called()
    execute.assert_not_called()
    assert deps.leadership.release_calls == 1

    deps, assignment, execute = fixture()
    deps.leadership.try_acquire = Mock(side_effect=RuntimeError("database unavailable"))
    with pytest.raises(RuntimeError):
        run_controller_once(deps, assignment, {"T"}, execute)
    deps.dispatch.claim.assert_not_called()
    execute.assert_not_called()
    assert deps.leadership.release_calls == 1


def test_non_postgres_assignment_is_rejected_before_acquisition():
    deps, assignment, execute = fixture()
    assignment.repository = object()
    with pytest.raises(TypeError, match="composed PostgreSQL workflow"):
        run_controller_once(deps, assignment, {"T"}, execute)
    assert deps.leadership.acquire_calls == 0
    deps.dispatch.claim.assert_not_called()


def test_positive_iteration_and_retired_instance_cannot_retry():
    deps, assignment, execute = fixture()
    assert run_controller_once(deps, assignment, {"T"}, execute)
    deps.dispatch.claim.assert_called_once_with({"T"}, 1800, track_pre_launch=True)
    assignment.get_coder_assignment.assert_called_once_with("T")
    deps.dispatch.finish.assert_called_once_with("dispatch", SHA)
    deps.dispatch.block.assert_not_called()
    assert deps.leadership.release_calls == 1
    assert not run_controller_once(deps, assignment, {"T"}, execute)
    deps.dispatch.claim.assert_called_once()


@pytest.mark.parametrize("lost_at", ["claim", "assignment", "execute", "finish"])
def test_loss_at_material_boundary_preserves_uncertain_slot(lost_at):
    deps, assignment, execute = fixture()
    operation = {
        "claim": deps.dispatch.claim,
        "assignment": assignment.get_coder_assignment,
        "execute": execute,
        "finish": deps.dispatch.finish,
    }[lost_at]
    original = operation.side_effect or operation.return_value

    def lose(*args, **kwargs):
        deps.leadership.held = False
        return original(*args, **kwargs) if callable(original) else original

    operation.side_effect = lose
    with pytest.raises(PermissionError, match="leadership lost"):
        run_controller_once(deps, assignment, {"T"}, execute)
    deps.dispatch.block.assert_not_called()
    assert deps.leadership.release_calls == 1
    if lost_at == "claim":
        assignment.get_coder_assignment.assert_not_called()
    if lost_at in {"claim", "assignment"}:
        execute.assert_not_called()
    if lost_at != "finish":
        deps.dispatch.finish.assert_not_called()


def test_callback_guard_stops_internal_effect_and_exception_blocks_only_while_owner():
    deps, assignment, _ = fixture()
    publish = Mock()

    def execute(_claim, _context, guard):
        deps.leadership.held = False
        guard()
        publish()
        return SHA

    with pytest.raises(PermissionError):
        run_controller_once(deps, assignment, {"T"}, execute)
    publish.assert_not_called()
    deps.dispatch.block.assert_not_called()

    deps, assignment, execute = fixture()
    execute.side_effect = RuntimeError("worker failed")
    with pytest.raises(RuntimeError, match="worker failed"):
        run_controller_once(deps, assignment, {"T"}, execute)
    deps.dispatch.block.assert_called_once_with("dispatch")
    assert deps.leadership.release_calls == 1


def test_cancellation_releases_session_and_retains_slot_on_loss():
    deps, assignment, execute = fixture()

    def cancelled(*args):
        deps.leadership.held = False
        raise KeyboardInterrupt

    execute.side_effect = cancelled
    with pytest.raises(KeyboardInterrupt):
        run_controller_once(deps, assignment, {"T"}, execute)
    deps.dispatch.block.assert_not_called()
    assert deps.leadership.release_calls == 1
