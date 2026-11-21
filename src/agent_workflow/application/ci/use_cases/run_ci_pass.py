"""Application coordination for exactly one fenced CI reconciliation pass."""

from agent_workflow.application.ci.ports import ICiLeadershipScope
from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
from agent_workflow.domain.ci_evidence import CiEvidence


class RunCiPassUseCase:
    """Use a fresh injected leadership scope; never create infrastructure here."""

    def __init__(
        self, reconciler: ReconcileCiUseCase, leadership: ICiLeadershipScope
    ) -> None:
        self._reconciler = reconciler
        self._leadership = leadership

    def execute(self, *, startup: bool = False) -> tuple[CiEvidence, ...]:
        with self._leadership.acquired_scope():
            return self._reconciler.execute(startup=startup)
