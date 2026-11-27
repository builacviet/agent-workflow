"""Track CI use case decisions and architecture boundary."""

import ast
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from agent_workflow.application.ci.ports import ICiBusinessWriteRepository
from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.ci_tracking import CiTrackingSnapshot
from agent_workflow.domain.exceptions import BusinessRuleError

HEAD = "a" * 40
OTHER = "b" * 40
REPOSITORY = "builacviet/agent-workflow"
DELIVERY = "12345678-1234-1234-1234-123456789abc"


def scope_with(operation):
    uow = MagicMock(spec=IUnitOfWork)
    scope = MagicMock()
    scope.__enter__.return_value = (uow, operation)
    return Mock(return_value=scope), uow, scope


def test_track_uses_locked_snapshot_and_single_scope():
    operation = Mock(spec=ICiBusinessWriteRepository)
    operation.load_for_tracking.return_value = CiTrackingSnapshot(
        "READY_IN_REVIEW", REPOSITORY, HEAD, None, None
    )
    provider, uow, scope = scope_with(operation)
    TrackCiTaskUseCase(provider).execute("task-1", 21, HEAD)
    operation.load_for_tracking.assert_called_once_with("task-1")
    operation.track.assert_called_once_with("task-1", 21, HEAD)
    provider.assert_called_once_with()
    uow.commit.assert_called_once_with()
    uow.__exit__.assert_called_once()
    scope.__exit__.assert_called_once()


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (
            CiTrackingSnapshot(None, None, None, None, None),
            "authorized exact task HEAD",
        ),
        (
            CiTrackingSnapshot("READY_IN_REVIEW", REPOSITORY, OTHER, None, None),
            "authorized exact task HEAD",
        ),
        (
            CiTrackingSnapshot("READY_IN_REVIEW", REPOSITORY, HEAD, 22, HEAD),
            "PR differs",
        ),
        (
            CiTrackingSnapshot("READY_IN_REVIEW", REPOSITORY, HEAD, 21, OTHER),
            "HEAD differs",
        ),
    ],
)
def test_rejected_tracking_does_not_write(snapshot, reason):
    operation = Mock(spec=ICiBusinessWriteRepository)
    operation.load_for_tracking.return_value = snapshot
    provider, uow, scope = scope_with(operation)
    with pytest.raises(BusinessRuleError, match=reason):
        TrackCiTaskUseCase(provider).execute("task-1", 21, HEAD)
    operation.track.assert_not_called()
    uow.commit.assert_not_called()
    assert scope.__exit__.call_count == 1


def test_ci_repository_does_not_depend_on_application_use_case_module():
    """The existing global AST import gate does not enforce this ownership."""
    root = Path(__file__).resolve().parents[4]
    source = (
        root
        / "src/agent_workflow/infrastructure/database/repositories/ci_state_repository.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    app_imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("agent_workflow.application")
    ]
    assert app_imports == ["agent_workflow.application.exceptions"]
    assert "require_matching_ci_binding" not in source
