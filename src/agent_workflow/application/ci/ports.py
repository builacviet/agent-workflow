"""CI feature ports: transactions, projections and trusted read-only transport."""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Protocol

from agent_workflow.application.ci.dtos import CiTrackedTaskSnapshot
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.ci_evidence import CiEvidence, WebhookNotification
from agent_workflow.domain.ci_tracking import (
    CiApprovalSnapshot,
    CiRecordSnapshot,
    CiTrackingSnapshot,
)


class ICiBusinessWriteRepository(Protocol):
    def load_for_tracking(self, task_id: str) -> CiTrackingSnapshot: ...

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None: ...

    def load_for_record(self, task_id: str) -> CiRecordSnapshot: ...

    def record(self, evidence: CiEvidence, verdict: str) -> None: ...


CiWriteScope = Callable[
    [], AbstractContextManager[tuple[IUnitOfWork, ICiBusinessWriteRepository]]
]


class ICiAcknowledgmentRepository(Protocol):
    def mark_processed(self, delivery_id: str) -> bool:
        """Settle a known delivery atomically; return False if it is unknown."""
        ...


class ICiApprovalReader(Protocol):
    def approved(self, task_id: str, head_sha: str) -> CiApprovalSnapshot | None: ...


class ICiReconciliationReader(Protocol):
    """Read projections needed to reconcile pending CI notifications."""

    def tracked(self, limit: int = 32) -> tuple[CiTrackedTaskSnapshot, ...]: ...

    def previous_failure(self, task_id: str, head_sha: str) -> bool: ...

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]: ...


class ICiStateReader(ICiApprovalReader, ICiReconciliationReader, Protocol):
    """CI read projections for approval and reconciliation."""


class ICiLeadershipScope(Protocol):
    """Run a single CI pass under a freshly acquired ownership lifetime."""

    def acquired_scope(self) -> AbstractContextManager[None]: ...

    def require_owner(self) -> None: ...


class ICiChecksReader(Protocol):
    """Trusted GitHub Checks transport, including complete pagination."""

    installation_id: int

    def get_pull_request(self, repository: str, number: int) -> Mapping: ...

    def list_check_runs(self, repository: str, sha: str) -> Mapping: ...
