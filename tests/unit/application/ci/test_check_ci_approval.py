"""Approve only exact-HEAD CI proof."""

from unittest.mock import Mock

import pytest

from agent_workflow.application.ci.use_cases.check_ci_approval import (
    CheckCiApprovalUseCase,
)
from agent_workflow.domain.ci_tracking import CiApprovalSnapshot

HEAD = "a" * 40
OTHER = "b" * 40
REPOSITORY = "builacviet/agent-workflow"


@pytest.mark.parametrize(
    ("verdict", "verified", "head", "expected"),
    [
        ("SUCCESS", True, HEAD, True),
        ("SUCCESS", False, HEAD, False),
        ("BLOCKED", True, HEAD, False),
        ("FAILURE", True, HEAD, False),
        ("SUCCESS", True, OTHER, False),
    ],
)
def test_approval_policy_requires_exact_head_and_proof(
    verdict, verified, head, expected
):
    reader = Mock()
    reader.approved.return_value = CiApprovalSnapshot(
        HEAD, verdict, HEAD, REPOSITORY, verified
    )
    assert CheckCiApprovalUseCase(reader).execute("task-1", head) is expected
    reader.approved.assert_called_once_with("task-1", head)


def test_missing_approval_is_false():
    reader = Mock()
    reader.approved.return_value = None
    assert not CheckCiApprovalUseCase(reader).execute("missing", HEAD)
