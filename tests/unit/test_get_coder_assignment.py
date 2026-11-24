"""Assignment uses the review snapshot and latest resolution for each finding."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent_workflow.application.review.dtos import (
    PreviousReview,
    ReviewFinding,
    ReviewTask,
)
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextOutput,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    GetCoderAssignmentUseCase,
    coder_assignment_response,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError


def test_latest_resolution_filters_findings_and_preserves_wire_context():
    finding = ReviewFinding(
        "r1", "f1", "T", "HIGH", "AC1", "src/a.py", 1, "issue", "fix", "verify"
    )
    review = PreviousReview(
        "r1",
        "T",
        1,
        "a" * 40,
        "b" * 40,
        WorkflowStatus.CHANGES_REQUESTED,
        "summary",
        "medium",
        None,
        "",
        "lead",
        1.0,
        None,
        (),
        (finding,),
    )
    task = ReviewTask(
        "T",
        "builacviet/agent-workflow",
        "task/t",
        "a" * 40,
        "b" * 40,
        "medium",
        0,
        2,
        1,
        WorkflowStatus.CHANGES_REQUESTED,
        0,
        1,
        ("docs/a.md",),
        "{}",
    )
    context = GetReviewContextOutput(
        task, 2, 1, review.reviewed_head_sha, review.verdict, (review,), ()
    )
    reader = Mock()
    reader.execute.return_value = context
    use = GetCoderAssignmentUseCase(reader)
    wire = coder_assignment_response(use.execute("T"))
    assert wire["unresolved_findings"][0]["resolution_status"] == "NOT_VERIFIED"
    assert wire["task_id"] == "T"
    resolved = replace(review, prior_resolutions=(("f1", "FIXED"),))
    reader.execute.return_value = replace(context, previous_reviews=(review, resolved))
    assert use.execute("T").unresolved_findings == ()


def test_blank_id_rejected_before_reader():
    reader = Mock()
    with pytest.raises(WorkflowError):
        GetCoderAssignmentUseCase(reader).execute(" ")
    reader.execute.assert_not_called()
