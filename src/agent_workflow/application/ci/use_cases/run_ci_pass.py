"""Application coordination for exactly one fenced CI reconciliation pass."""

from dataclasses import dataclass

from agent_workflow.application.ci.ports import ICiLeadershipScope
from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase


@dataclass(frozen=True, slots=True)
class CiPassResult:
    """Observation counts from a completed CI pass."""

    observed_count: int
    blocked_count: int


class RunCiPassUseCase:
    """Use a fresh injected leadership scope; never create infrastructure here."""

    def __init__(
        self, reconciler: ReconcileCiUseCase, leadership: ICiLeadershipScope
    ) -> None:
        self._reconciler = reconciler
        self._leadership = leadership

    def execute(self, *, startup: bool = False) -> CiPassResult:
        with self._leadership.acquired_scope():
            observations = self._reconciler.execute(startup=startup)
        return CiPassResult(
            observed_count=len(observations),
            blocked_count=sum(
                evidence.verdict == "BLOCKED" for evidence in observations
            ),
        )
