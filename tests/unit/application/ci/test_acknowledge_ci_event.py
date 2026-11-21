"""ACK signed webhook only after valid lookup inside transaction."""

from unittest.mock import MagicMock, Mock

import pytest

from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
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
