"""Deterministic guarded PostgreSQL Controller lifecycle tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.application.exceptions import DispatchCleanupUnconfirmedError
from agent_workflow.composition.postgres_controller import (
    ControllerDependencies,
)

SHA = "a" * 40
CLAIM = DispatchClaim(
    dispatch_id="dispatch",
    task_id="T",
    round_number=0,
    input_sha=SHA,
    deadline=9999999999.0,
)
ASSIGNMENT = {
    "task_id": "T",
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
    dispatch.claim.return_value = CLAIM
    assignment = Mock()
    assignment.get_coder_assignment.return_value = ASSIGNMENT.copy()
    execute = Mock(return_value=SimpleNamespace(head_sha=SHA))
    deps = ControllerDependencies(
        handoff_provider=Mock(),
        dispatch=dispatch,
        ci_tracking=Mock(),
        ci_write_scope=Mock(),
        ci_factory=Mock(),
        leadership=leader,
    )
    return deps, assignment, execute


def test_denied_and_acquisition_error_do_no_work_and_release():
    deps, assignment, execute = fixture(False)
    dispatch_pass = RunDispatchPassUseCase(
        deps.leadership, deps.dispatch, assignment, Mock(execute=execute)
    )
    assert not dispatch_pass.execute({"T"})
    deps.dispatch.claim.assert_not_called()
    assignment.get_coder_assignment.assert_not_called()
    execute.assert_not_called()
    assert deps.leadership.release_calls == 1

    deps, assignment, execute = fixture()
    deps.leadership.try_acquire = Mock(side_effect=RuntimeError("database unavailable"))
    dispatch_pass = RunDispatchPassUseCase(
        deps.leadership, deps.dispatch, assignment, Mock(execute=execute)
    )
    with pytest.raises(RuntimeError):
        dispatch_pass.execute({"T"})
    deps.dispatch.claim.assert_not_called()
    execute.assert_not_called()
    assert deps.leadership.release_calls == 1


def test_positive_iteration_and_retired_instance_cannot_retry():
    deps, assignment, execute = fixture()
    dispatch_pass = RunDispatchPassUseCase(
        deps.leadership, deps.dispatch, assignment, Mock(execute=execute)
    )
    assert dispatch_pass.execute({"T"})
    deps.dispatch.claim.assert_called_once_with({"T"}, 1800, track_pre_launch=True)
    assignment.get_coder_assignment.assert_called_once_with("T")
    deps.dispatch.finish.assert_called_once_with("dispatch", SHA)
    deps.dispatch.block.assert_not_called()
    assert deps.leadership.release_calls == 1
    assert not dispatch_pass.execute({"T"})
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
    dispatch_pass = RunDispatchPassUseCase(
        deps.leadership, deps.dispatch, assignment, Mock(execute=execute)
    )
    with pytest.raises(PermissionError, match="leadership lost"):
        dispatch_pass.execute({"T"})
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
        return SimpleNamespace(head_sha=SHA)

    dispatch_pass = RunDispatchPassUseCase(
        deps.leadership, deps.dispatch, assignment, Mock(execute=execute)
    )
    with pytest.raises(PermissionError):
        dispatch_pass.execute({"T"})
    publish.assert_not_called()
    deps.dispatch.block.assert_not_called()

    deps, assignment, execute = fixture()
    execute.side_effect = RuntimeError("worker failed")
    dispatch_pass = RunDispatchPassUseCase(
        deps.leadership, deps.dispatch, assignment, Mock(execute=execute)
    )
    with pytest.raises(RuntimeError, match="worker failed"):
        dispatch_pass.execute({"T"})
    deps.dispatch.block.assert_called_once_with("dispatch")
    assert deps.leadership.release_calls == 1


def test_cancellation_releases_session_and_retains_slot_on_loss():
    deps, assignment, execute = fixture()

    def cancelled(*args):
        deps.leadership.held = False
        raise KeyboardInterrupt

    execute.side_effect = cancelled
    dispatch_pass = RunDispatchPassUseCase(
        deps.leadership, deps.dispatch, assignment, Mock(execute=execute)
    )
    with pytest.raises(KeyboardInterrupt):
        dispatch_pass.execute({"T"})
    deps.dispatch.block.assert_not_called()
    assert deps.leadership.release_calls == 1


@pytest.mark.parametrize("failure", [None, "cleanup", "finish", "lost"])
def test_post_finish_cleanup_is_owned_and_cannot_rewrite_terminal_state(failure):
    deps, assignment, execute = fixture()
    events = []

    def finish(*args):
        events.append("finish")
        if failure == "finish":
            raise RuntimeError("finish uncertain")
        if failure == "lost":
            deps.leadership.held = False

    def cleanup(dispatch_id, result):
        assert deps.leadership.is_held()
        assert deps.leadership.release_calls == 0
        assert dispatch_id == "dispatch"
        assert result is execute.return_value
        events.append("cleanup")
        if failure == "cleanup":
            raise RuntimeError("pin retained")

    deps.dispatch.finish.side_effect = finish
    dispatch_pass = RunDispatchPassUseCase(
        deps.leadership,
        deps.dispatch,
        assignment,
        Mock(execute=execute, release_engine=cleanup),
    )
    if failure:
        expected = {
            "cleanup": DispatchCleanupUnconfirmedError,
            "finish": RuntimeError,
            "lost": PermissionError,
        }[failure]
        with pytest.raises(expected):
            dispatch_pass.execute({"T"})
    else:
        assert dispatch_pass.execute({"T"})
    assert events == (
        ["finish", "cleanup"] if failure in {None, "cleanup"} else ["finish"]
    )
    assert deps.leadership.release_calls == 1
    assert deps.dispatch.block.call_count == (1 if failure == "finish" else 0)
    execute.assert_called_once()
