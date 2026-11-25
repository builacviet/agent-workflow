"""Record trusted CI evidence and sticky failure tests."""

from unittest.mock import MagicMock, Mock

import pytest

from agent_workflow.application.ci.ports import ICiBusinessWriteRepository
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.ci_evidence import CiEvidence
from agent_workflow.domain.ci_tracking import CiRecordSnapshot
from agent_workflow.domain.workflow_identity import WorkflowError

HEAD = "a" * 40
OTHER = "b" * 40
REPOSITORY = "builacviet/agent-workflow"
DELIVERY = "12345678-1234-1234-1234-123456789abc"


def scope_with(operation):
    uow = MagicMock(spec=IUnitOfWork)
    scope = MagicMock()
    scope.__enter__.return_value = (uow, operation)
    return Mock(return_value=scope), uow, scope


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
    operation = Mock(spec=ICiBusinessWriteRepository)
    operation.load_for_record.return_value = CiRecordSnapshot(
        REPOSITORY, HEAD, 21, HEAD, had_failure
    )
    provider, uow, scope = scope_with(operation)
    ev = evidence(incoming)
    RecordCiEvidenceUseCase(provider).execute(ev)
    operation.load_for_record.assert_called_once_with("task-1")
    operation.record.assert_called_once_with(ev, expected)
    provider.assert_called_once_with()
    uow.commit.assert_called_once_with()
    uow.__exit__.assert_called_once()
    scope.__exit__.assert_called_once()


def test_record_rejects_stale_head_before_inserting():
    operation = Mock(spec=ICiBusinessWriteRepository)
    operation.load_for_record.return_value = CiRecordSnapshot(
        REPOSITORY, OTHER, 21, HEAD, False
    )
    provider, uow, _ = scope_with(operation)
    with pytest.raises(WorkflowError, match="mismatched"):
        RecordCiEvidenceUseCase(provider).execute(evidence())
    operation.record.assert_not_called()
    uow.commit.assert_not_called()
