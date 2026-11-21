"""Inbound CI state adapter; delegates to injected Application Use Cases.

Concrete database types and factories are deliberately absent. Legacy runtime
callers may depend on this Controller-facing API, but DI belongs to Composition.
"""

from agent_workflow.application.ci.ports import ICiStateReader
from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.application.ci.use_cases.check_ci_approval import (
    CheckCiApprovalUseCase,
)
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.domain.ci_evidence import CiEvidence, WebhookNotification


class CiStateAdapter:
    """Facade protocol for Controller and runtime callers, without own wiring."""

    def __init__(
        self,
        *,
        reader: ICiStateReader,
        tracker: TrackCiTaskUseCase,
        recorder: RecordCiEvidenceUseCase,
        approval: CheckCiApprovalUseCase,
        acknowledger: AcknowledgeCiEventUseCase,
    ) -> None:
        self._reader = reader
        self._tracker = tracker
        self._recorder = recorder
        self._approval = approval
        self._acknowledger = acknowledger

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None:
        self._tracker.execute(task_id, pr_number, head_sha)

    def tracked(self, limit: int = 32) -> list[dict]:
        return self._reader.tracked(limit)

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        return self._reader.previous_failure(task_id, head_sha)

    def record(self, evidence: CiEvidence) -> None:
        self._recorder.execute(evidence)

    def approved(self, task_id: str, head_sha: str) -> bool:
        return self._approval.execute(task_id, head_sha)

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        return self._reader.pending_events(limit)

    def mark_processed(self, delivery_id: str) -> None:
        self._acknowledger.execute(delivery_id)
