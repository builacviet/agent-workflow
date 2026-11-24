"""Tests of handoff decisions through the scoped application port."""

from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest

from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.application.task.use_cases.register_task_handoff import (
    IHandoffRepository,
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
    repo = Mock(spec=IHandoffRepository)
    uow = MagicMock(spec=IUnitOfWork)
    repo.lock_task.return_value = task
    repo.handoff_path.return_value = (False, None)
    repo.advance_failed_ci_head.return_value = 7

    return RegisterTaskHandoffUseCase(uow, repo, clock=lambda: 123.0), repo, uow


def test_first_handoff_records_atomic_transition():
    use, repo, uow = setup()
    result = use.execute(REQUEST)
    assert (result.task_id, result.status, result.head_sha) == (
        "T",
        WorkflowStatus.READY_IN_REVIEW,
        HEAD,
    )
    uow.commit.assert_called_once_with()
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
        replace(REQUEST, base_sha=NEW),
    ],
)
def test_invalid_request_never_records(invalid_request):
    use, repo, uow = setup()
    with pytest.raises(BusinessRuleError):
        use.execute(invalid_request)
    repo.record.assert_not_called()
    uow.commit.assert_not_called()
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
    use, repo, uow = setup()
    with pytest.raises(BusinessRuleError):
        use.execute(invalid_request)
    uow.__enter__.assert_not_called()
    assert not repo.mock_calls
    uow.commit.assert_not_called()


@pytest.mark.parametrize(
    "task",
    [
        replace(TASK, status=WorkflowStatus.APPROVED),
        replace(TASK, completed_review_rounds=2),
        replace(TASK, status=WorkflowStatus.CHANGES_REQUESTED, head_sha=HEAD),
    ],
)
def test_disallowed_state_never_records(task):
    use, repo, uow = setup(task)
    with pytest.raises(BusinessRuleError):
        use.execute(REQUEST)
    repo.record.assert_not_called()
    uow.commit.assert_not_called()


def test_unknown_task_never_commits():
    use, repo, uow = setup(task=None)
    with pytest.raises(BusinessRuleError, match="unknown authorized task"):
        use.execute(REQUEST)
    repo.lock_task.assert_called_once_with(REQUEST.task_id)
    repo.record.assert_not_called()
    uow.commit.assert_not_called()


def test_same_head_is_idempotent():
    use, repo, uow = setup(
        replace(TASK, status=WorkflowStatus.READY_IN_REVIEW, head_sha=HEAD)
    )
    repo.handoff_path.return_value = (True, REQUEST.handoff_file_path)
    assert use.execute(REQUEST).head_sha == HEAD
    repo.record.assert_not_called()
    uow.commit.assert_not_called()
    repo.advance_failed_ci_head.assert_not_called()


def test_repair_requires_reservation_and_records_metadata():
    use, repo, uow = setup(
        replace(TASK, status=WorkflowStatus.READY_IN_REVIEW, head_sha=HEAD)
    )
    repo.handoff_path.side_effect = [(True, REQUEST.handoff_file_path), (False, None)]
    use.execute(replace(REQUEST, new_head_sha=NEW))
    repo.advance_failed_ci_head.assert_called_once_with("T", HEAD, NEW)
    assert repo.record.call_args.args[2] == TaskEventAction.CI_REPAIR_HANDOFF
    assert repo.record.call_args.args[3]["previous_head_sha"] == HEAD
    assert repo.record.call_args.args[3]["pr_number"] == 7
    uow.commit.assert_called_once_with()


def test_failed_reservation_never_records():
    use, repo, uow = setup(
        replace(TASK, status=WorkflowStatus.READY_IN_REVIEW, head_sha=HEAD)
    )
    repo.handoff_path.side_effect = [(True, REQUEST.handoff_file_path), (False, None)]
    repo.advance_failed_ci_head.side_effect = BusinessRuleError("not authorized")
    with pytest.raises(BusinessRuleError):
        use.execute(replace(REQUEST, new_head_sha=NEW))
    repo.record.assert_not_called()
    uow.commit.assert_not_called()
