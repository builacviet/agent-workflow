"""Tests of handoff decisions through the scoped application port."""

from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent_workflow.application.task.use_cases.register_task_handoff import (
    RegisterTaskHandoffUseCase,
)
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.task.handoff import (
    HandoffRequest,
    HandoffTask,
    TaskEventAction,
)

BASE = "a" * 40
HEAD = "b" * 40
NEW = "c" * 40
REQUEST = HandoffRequest(
    "T", "builacviet/agent-workflow", "task/t", BASE, HEAD, "docs/handoff.md", "passed"
)
TASK = HandoffTask(
    "T",
    REQUEST.repository,
    REQUEST.branch,
    BASE,
    None,
    WorkflowStatus.AWAITING_HANDOFF,
    0,
    (REQUEST.handoff_file_path,),
)


def setup(task=TASK):
    repo = Mock()
    repo.lock_task.return_value = task
    repo.handoff_path.return_value = (False, None)
    repo.advance_failed_ci_head.return_value = 7

    @contextmanager
    def scope():
        yield repo

    return RegisterTaskHandoffUseCase(scope, clock=lambda: 123.0), repo


def test_first_handoff_records_atomic_transition():
    use, repo = setup()
    result = use.execute(REQUEST)
    assert (result.task_id, result.status, result.head_sha) == (
        "T",
        WorkflowStatus.READY_IN_REVIEW,
        HEAD,
    )
    task, request, action, metadata, now = repo.record.call_args.args
    assert (task, request, action, now) == (
        TASK,
        REQUEST,
        TaskEventAction.HANDOFF,
        123.0,
    )
    assert metadata == {
        "handoff_file_path": "docs/handoff.md",
        "test_summary": "passed",
        "verification": "reporter_supplied_not_remotely_verified",
    }


@pytest.mark.parametrize(
    "invalid_request",
    [
        replace(REQUEST, new_head_sha="bad"),
        replace(REQUEST, test_summary=" "),
        replace(REQUEST, repository="other/repo"),
        replace(REQUEST, handoff_file_path="other"),
        replace(REQUEST, branch="other"),
    ],
)
def test_invalid_request_never_records(invalid_request):
    use, repo = setup()
    with pytest.raises(BusinessRuleError):
        use.execute(invalid_request)
    repo.record.assert_not_called()
    repo.advance_failed_ci_head.assert_not_called()


@pytest.mark.parametrize(
    "invalid_request",
    [
        replace(REQUEST, repository=" "),
        replace(REQUEST, repository="invalid"),
        replace(REQUEST, branch=" "),
        replace(REQUEST, base_sha="bad"),
        replace(REQUEST, handoff_file_path=""),
        replace(REQUEST, handoff_file_path="../outside"),
    ],
)
def test_invalid_shape_rejected_before_scope(invalid_request):
    scope = Mock(side_effect=AssertionError("invalid command opened a scope"))
    use = RegisterTaskHandoffUseCase(scope, clock=lambda: 123.0)
    with pytest.raises(BusinessRuleError):
        use.execute(invalid_request)
    scope.assert_not_called()


@pytest.mark.parametrize(
    "task",
    [
        replace(TASK, status=WorkflowStatus.APPROVED),
        replace(TASK, completed_review_rounds=2),
        replace(TASK, status=WorkflowStatus.CHANGES_REQUESTED, head_sha=HEAD),
    ],
)
def test_disallowed_state_never_records(task):
    use, repo = setup(task)
    with pytest.raises(BusinessRuleError):
        use.execute(REQUEST)
    repo.record.assert_not_called()


def test_same_head_is_idempotent():
    use, repo = setup(
        replace(TASK, status=WorkflowStatus.READY_IN_REVIEW, head_sha=HEAD)
    )
    repo.handoff_path.return_value = (True, REQUEST.handoff_file_path)
    assert use.execute(REQUEST).head_sha == HEAD
    repo.record.assert_not_called()
    repo.advance_failed_ci_head.assert_not_called()


def test_repair_requires_reservation_and_records_metadata():
    use, repo = setup(
        replace(TASK, status=WorkflowStatus.READY_IN_REVIEW, head_sha=HEAD)
    )
    repo.handoff_path.side_effect = [(True, REQUEST.handoff_file_path), (False, None)]
    use.execute(replace(REQUEST, new_head_sha=NEW))
    repo.advance_failed_ci_head.assert_called_once_with("T", HEAD, NEW)
    assert repo.record.call_args.args[2] == TaskEventAction.CI_REPAIR_HANDOFF
    assert repo.record.call_args.args[3]["previous_head_sha"] == HEAD
    assert repo.record.call_args.args[3]["pr_number"] == 7


def test_failed_reservation_never_records():
    use, repo = setup(
        replace(TASK, status=WorkflowStatus.READY_IN_REVIEW, head_sha=HEAD)
    )
    repo.handoff_path.side_effect = [(True, REQUEST.handoff_file_path), (False, None)]
    repo.advance_failed_ci_head.side_effect = BusinessRuleError("not authorized")
    with pytest.raises(BusinessRuleError):
        use.execute(replace(REQUEST, new_head_sha=NEW))
    repo.record.assert_not_called()
