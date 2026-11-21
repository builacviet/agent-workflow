"""Unit regressions for full CI Application decisions and transaction boundary."""

import ast
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from agent_workflow.application.ci_tracking import (
    AcknowledgeCiEventUseCase,
    CheckCiApprovalUseCase,
    RecordCiEvidenceUseCase,
    TrackCiTaskUseCase,
)
from agent_workflow.domain.ci_evidence import CiEvidence
from agent_workflow.domain.ci_tracking import (
    CiApprovalSnapshot,
    CiRecordSnapshot,
    CiTrackingSnapshot,
)
from agent_workflow.domain.workflow_identity import WorkflowError

HEAD = "a" * 40
OTHER = "b" * 40
REPOSITORY = "builacviet/agent-workflow"
DELIVERY = "12345678-1234-1234-1234-123456789abc"


def scope_with(operation):
    scope = MagicMock()
    scope.__enter__.return_value = operation
    repository = Mock()
    repository.transaction.return_value = scope
    return repository, scope


def evidence(verdict="SUCCESS"):
    return CiEvidence(
        "task-1",
        REPOSITORY,
        17,
        21,
        HEAD,
        HEAD,
        HEAD,
        "Python tests",
        "github-actions",
        verdict,
        {
            "SUCCESS": "REQUIRED_CHECK_PASSED",
            "FAILURE": "REQUIRED_CHECK_FAILED",
            "BLOCKED": "CHECK_PENDING",
        }[verdict],
        91,
        92,
        1,
        "2026-11-10T00:00:00Z",
        "2026-11-10T00:01:00Z",
    )


def test_track_uses_locked_snapshot_and_single_scope():
    operation = Mock()
    operation.load_for_tracking.return_value = CiTrackingSnapshot(
        "READY_IN_REVIEW", REPOSITORY, HEAD, None, None
    )
    repository, scope = scope_with(operation)
    TrackCiTaskUseCase(repository).execute("task-1", 21, HEAD)
    operation.load_for_tracking.assert_called_once_with("task-1")
    operation.track.assert_called_once_with("task-1", 21, HEAD)
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
    operation = Mock()
    operation.load_for_tracking.return_value = snapshot
    repository, scope = scope_with(operation)
    with pytest.raises(WorkflowError, match=reason):
        TrackCiTaskUseCase(repository).execute("task-1", 21, HEAD)
    operation.track.assert_not_called()
    assert scope.__exit__.call_count == 1


@pytest.mark.parametrize(
    ("had_failure", "incoming", "expected"),
    [
        (False, "SUCCESS", "SUCCESS"),
        (False, "BLOCKED", "BLOCKED"),
        (False, "FAILURE", "FAILURE"),
        (True, "SUCCESS", "FAILURE"),
        (True, "BLOCKED", "FAILURE"),
    ],
)
def test_record_verdict_is_domain_owned(had_failure, incoming, expected):
    operation = Mock()
    operation.load_for_record.return_value = CiRecordSnapshot(
        REPOSITORY, HEAD, 21, HEAD, had_failure
    )
    repo, scope = scope_with(operation)
    ev = evidence(incoming)
    RecordCiEvidenceUseCase(repo).execute(ev)
    operation.load_for_record.assert_called_once_with("task-1")
    operation.record.assert_called_once_with(ev, expected)
    scope.__exit__.assert_called_once()


def test_record_rejects_stale_head_before_inserting():
    operation = Mock()
    operation.load_for_record.return_value = CiRecordSnapshot(
        REPOSITORY, OTHER, 21, HEAD, False
    )
    repo, _ = scope_with(operation)
    with pytest.raises(WorkflowError, match="mismatched"):
        RecordCiEvidenceUseCase(repo).execute(evidence())
    operation.record.assert_not_called()


def test_ack_requires_locked_inbox_but_is_idempotent_at_persistence():
    operation = Mock()
    operation.inbox_delivery_exists.return_value = False
    repo, _ = scope_with(operation)
    with pytest.raises(WorkflowError, match="unknown webhook delivery"):
        AcknowledgeCiEventUseCase(repo).execute(DELIVERY)
    operation.mark_processed.assert_not_called()
    operation.inbox_delivery_exists.return_value = True
    AcknowledgeCiEventUseCase(repo).execute(DELIVERY)
    operation.mark_processed.assert_called_once_with(DELIVERY)


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
def test_approval_policy_requires_exact_head_and_proof(verdict, verified, head, expected):
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


def test_ci_repository_does_not_depend_on_application_use_case_module():
    """The existing global AST import gate does not enforce this ownership."""
    root = Path(__file__).resolve().parents[1]
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
