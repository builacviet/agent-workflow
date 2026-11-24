from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from agent_workflow.application.review.dtos import ReviewQueueItem
from agent_workflow.application.review.use_cases.list_review_queue import (
    ListReviewQueueUseCase,
)
from agent_workflow.domain.workflow_identity import WorkflowError


def test_recovery_and_queue_read_share_the_scope():
    item = ReviewQueueItem("T", "Task", "owner/repo", "branch", None, "high", 1)
    calls = Mock()
    calls.reader.read.return_value = (item,)

    @contextmanager
    def scope():
        calls.enter()
        try:
            yield calls.recover, calls.reader
        finally:
            calls.exit()

    assert ListReviewQueueUseCase(scope).execute("high") == (item,)
    assert [call[0] for call in calls.mock_calls] == [
        "enter",
        "recover",
        "reader.read",
        "exit",
    ]
    calls.reader.read.assert_called_once_with("high")


def test_invalid_effort_type_does_not_open_scope():
    scope = Mock()
    with pytest.raises(WorkflowError, match="requested_effort must be a string"):
        ListReviewQueueUseCase(scope).execute(1)
    scope.assert_not_called()
