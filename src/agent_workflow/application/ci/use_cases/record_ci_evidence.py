"""Layer: Application
Responsibility: Record trusted CI evidence and sticky same-HEAD failure atomically.
Dependencies: CI write scope port and domain evidence policy.
"""

from agent_workflow.application.ci.ports import CiWriteScope
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.domain.ci_evidence import CiEvidence, validate_evidence
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.domain.workflow_identity import validate_sha


class RecordCiEvidenceUseCase:
    def __init__(self, scope: CiWriteScope) -> None:
        self._scope = scope

    def execute(self, evidence: CiEvidence) -> None:
        validate_evidence(evidence)
        try:
            validate_sha(evidence.expected_head_sha)
        except DomainError:
            raise InvalidApplicationRequestError("invalid CI evidence HEAD") from None
        if type(evidence.pr_number) is not int or evidence.pr_number < 1:
            raise InvalidApplicationRequestError("invalid CI evidence PR")
        with self._scope() as (uow, repository), uow:
            state = repository.load_for_record(evidence.task_id)
            verdict = state.verdict_for(evidence)
            repository.record(evidence, verdict)
            uow.commit()
