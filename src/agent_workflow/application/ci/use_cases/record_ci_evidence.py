"""Record trusted CI evidence and sticky same-HEAD failure atomically."""

from agent_workflow.application.ci.ports import ICiWriteRepository
from agent_workflow.domain.ci_evidence import CiEvidence, validate_evidence
from agent_workflow.domain.workflow_identity import WorkflowError, validate_sha


class RecordCiEvidenceUseCase:
    def __init__(self, repository: ICiWriteRepository) -> None:
        self._repository = repository

    def execute(self, evidence: CiEvidence) -> None:
        validate_evidence(evidence)
        validate_sha(evidence.expected_head_sha)
        if type(evidence.pr_number) is not int or evidence.pr_number < 1:
            raise WorkflowError("invalid CI evidence PR")
        with self._repository.transaction() as tx:
            state = tx.load_for_record(evidence.task_id)
            verdict = state.verdict_for(evidence)
            tx.record(evidence, verdict)
