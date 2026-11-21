"""Controller CI use cases and their transaction/read ports.

All business decisions happen here or in the pure Domain snapshots.
The persistence adapter supplies one atomic transaction per write operation.
"""

from contextlib import AbstractContextManager
from typing import Protocol

from agent_workflow.domain.ci_evidence import (
    CiEvidence,
    validate_delivery_id,
    validate_evidence,
)
from agent_workflow.domain.ci_tracking import (
    CiApprovalSnapshot,
    CiRecordSnapshot,
    CiTrackingSnapshot,
)
from agent_workflow.domain.workflow_identity import WorkflowError, validate_sha


class ICiWriteTransaction(Protocol):
    def load_for_tracking(self, task_id: str) -> CiTrackingSnapshot: ...

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None: ...

    def load_for_record(self, task_id: str) -> CiRecordSnapshot: ...

    def record(self, evidence: CiEvidence, verdict: str) -> None: ...

    def inbox_delivery_exists(self, delivery_id: str) -> bool: ...

    def mark_processed(self, delivery_id: str) -> None: ...


class ICiWriteRepository(Protocol):
    def transaction(self) -> AbstractContextManager[ICiWriteTransaction]: ...


class ICiApprovalReader(Protocol):
    def approved(self, task_id: str, head_sha: str) -> CiApprovalSnapshot | None: ...


class TrackCiTaskUseCase:
    def __init__(self, repository: ICiWriteRepository) -> None:
        self._repository = repository

    def execute(self, task_id: str, pr_number: int, head_sha: str) -> None:
        validate_sha(head_sha)
        if (
            not isinstance(task_id, str)
            or not task_id
            or type(pr_number) is not int
            or not 1 <= pr_number < 2**31
        ):
            raise WorkflowError("invalid CI tracking identity")
        with self._repository.transaction() as tx:
            snapshot = tx.load_for_tracking(task_id)
            if snapshot.needs_binding(pr_number, head_sha):
                tx.track(task_id, pr_number, head_sha)


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


class AcknowledgeCiEventUseCase:
    def __init__(self, repository: ICiWriteRepository) -> None:
        self._repository = repository

    def execute(self, delivery_id: str) -> None:
        validate_delivery_id(delivery_id)
        with self._repository.transaction() as tx:
            if not tx.inbox_delivery_exists(delivery_id):
                raise WorkflowError("unknown webhook delivery")
            tx.mark_processed(delivery_id)


class CheckCiApprovalUseCase:
    def __init__(self, reader: ICiApprovalReader) -> None:
        self._reader = reader

    def execute(self, task_id: str, head_sha: str) -> bool:
        state = self._reader.approved(task_id, head_sha)
        return state.approves(head_sha) if state is not None else False
