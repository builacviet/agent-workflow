from unittest.mock import Mock

import pytest

from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
    GetReviewContextUseCase,
)
from agent_workflow.application.review.use_cases.get_review_result import (
    GetReviewResultUseCase,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        (WorkflowStatus.APPROVED, None),
        (WorkflowStatus.ESCALATED, "Unresolved finding"),
    ],
)
def test_result_derives_fields_from_one_context(status, expected_reason):
    context_use_case = Mock(spec=GetReviewContextUseCase)
    context = context_use_case.execute.return_value
    context.task.status = status
    context.previous_reviews = (
        Mock(actual_effort="medium", summary="Earlier review"),
        Mock(actual_effort="high", summary="Unresolved finding"),
    )
    request = GetReviewContextInput("TEST")

    result = GetReviewResultUseCase(context_use_case).execute(request)

    context_use_case.execute.assert_called_once_with(request)
    assert result.context is context
    assert result.actual_effort == "high"
    assert result.escalation_reason == expected_reason


def test_result_preserves_unknown_authorized_task_error():
    context_use_case = Mock(spec=GetReviewContextUseCase)
    context_use_case.execute.side_effect = WorkflowError("unknown authorized task")
    with pytest.raises(WorkflowError, match="^unknown authorized task$"):
        GetReviewResultUseCase(context_use_case).execute(
            GetReviewContextInput("missing")
        )
