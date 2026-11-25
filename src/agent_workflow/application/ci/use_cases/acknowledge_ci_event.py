"""Layer: Application
Responsibility: Acknowledge a known signed delivery after terminal reconciliation.
Dependencies: CI acknowledgment port and domain delivery validation.
"""

from agent_workflow.application.ci.ports import ICiAcknowledgmentRepository
from agent_workflow.domain.ci_evidence import validate_delivery_id
from agent_workflow.domain.exceptions import BusinessRuleError


class AcknowledgeCiEventUseCase:
    def __init__(self, repository: ICiAcknowledgmentRepository) -> None:
        self._repository = repository

    def execute(self, delivery_id: str) -> None:
        validate_delivery_id(delivery_id)
        if not self._repository.mark_processed(delivery_id):
            raise BusinessRuleError("unknown webhook delivery")
