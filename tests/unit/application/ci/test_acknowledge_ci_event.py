"""ACK validates identity and interprets atomic persistence outcomes."""

from unittest.mock import Mock

import pytest

from agent_workflow.application.ci.ports import ICiAcknowledgmentRepository
from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.domain.exceptions import BusinessRuleError

DELIVERY = "12345678-1234-1234-1234-123456789abc"


def test_ack_requires_known_delivery_and_accepts_replay():
    repository = Mock(spec=ICiAcknowledgmentRepository)
    repository.mark_processed.return_value = False
    use_case = AcknowledgeCiEventUseCase(repository)

    with pytest.raises(BusinessRuleError, match="unknown webhook delivery"):
        use_case.execute(DELIVERY)
    repository.mark_processed.assert_called_once_with(DELIVERY)

    repository.reset_mock()
    repository.mark_processed.return_value = True
    use_case.execute(DELIVERY)
    repository.mark_processed.assert_called_once_with(DELIVERY)


def test_invalid_delivery_identity_never_reaches_repository():
    repository = Mock(spec=ICiAcknowledgmentRepository)

    with pytest.raises(BusinessRuleError, match="invalid webhook delivery identity"):
        AcknowledgeCiEventUseCase(repository).execute(DELIVERY.upper())

    repository.mark_processed.assert_not_called()
