"""Typed Presentation boundary for Controller CI one-shot and watch commands.

The CLI owns argument interpretation, result display and retry scheduling.
A composition-provided operation performs the actual guarded reconciliation.
No PostgreSQL, GitHub clients, or Domain evidence enter this API.
"""

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass

from agent_workflow.application.ci.use_cases.run_ci_pass import RunCiPassUseCase
from agent_workflow.domain.ci_evidence import CiEvidence


@dataclass(frozen=True, slots=True)
class CiRunRequest:
    """One Controller CI pass; startup may recheck durable tracked work."""

    startup: bool


@dataclass(frozen=True, slots=True)
class CiRunResponse:
    """Presentation projection of the durable, completed CI pass."""

    observed_count: int
    blocked_count: int


CiRunOperation = Callable[[CiRunRequest], CiRunResponse]
sleep = time.sleep


class CiController:
    """Inbound adapter: request DTO → injected Application UC → response DTO."""

    def __init__(self, use_case: RunCiPassUseCase) -> None:
        self._use_case = use_case

    def reconcile(self, request: CiRunRequest) -> CiRunResponse:
        observations = self._use_case.execute(startup=request.startup)
        return present_ci_observations(observations)


def present_ci_observations(
    observations: tuple[CiEvidence, ...],
) -> CiRunResponse:
    """Map trusted Application evidence into the CLI response DTO."""
    return CiRunResponse(
        observed_count=len(observations),
        blocked_count=sum(e.verdict == "BLOCKED" for e in observations),
    )


def add_ci_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the two explicit CI-only operator modes on the shared parser."""
    parser.add_argument(
        "--ci-once",
        action="store_true",
        help="reconcile CI only, never dispatch agents",
    )
    parser.add_argument(
        "--ci-watch",
        action="store_true",
        help="consume local inbox, never dispatch agents",
    )


def run_ci_once(reconcile: CiRunOperation) -> None:
    """Print only after a successful committed pass."""
    result = reconcile(CiRunRequest(startup=True))
    print(
        f"CI reconciliation: {result.observed_count} tracked observations",
        flush=True,
    )


def run_ci_watch(
    reconcile: CiRunOperation,
    *,
    sleep: Callable[[float], None] = sleep,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Bounded backoff on BLOCKED; never swallow a lost leadership exception."""
    delay = 2
    startup = True
    while stop is None or not stop():
        result = reconcile(CiRunRequest(startup=startup))
        startup = False
        delay = min(60, delay * 2) if result.blocked_count else 2
        if stop is not None and stop():
            break
        sleep(delay)
