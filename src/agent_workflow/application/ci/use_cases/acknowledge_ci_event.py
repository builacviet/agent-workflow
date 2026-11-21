"""Acknowledge a known signed webhook delivery after terminal reconciliation."""

from agent_workflow.application.ci.ports import ICiWriteRepository
from agent_workflow.domain.ci_evidence import validate_delivery_id
from agent_workflow.domain.exceptions import DomainError


class AcknowledgeCiEventUseCase:
    def __init__(self, repository: ICiWriteRepository) -> None:
        self._repository = repository

    def execute(self, delivery_id: str) -> None:
        validate_delivery_id(delivery_id)
        with self._repository.transaction() as tx:
            if not tx.inbox_delivery_exists(delivery_id):
                raise DomainError("unknown webhook delivery")
            tx.mark_processed(delivery_id)
