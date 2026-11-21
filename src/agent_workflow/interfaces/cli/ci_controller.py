"""Presentation adapter for PostgreSQL CI Controller one-shot and watch modes.

No database, GitHub client, factory or composition imports. A composed callback
executes exactly one leadership-fenced reconciliation pass on demand.
"""

import argparse
import time
from collections.abc import Callable

from agent_workflow.domain.ci_evidence import CiEvidence

CiPass = Callable[[bool], tuple[CiEvidence, ...]]
sleep = time.sleep


def add_ci_arguments(parser: argparse.ArgumentParser) -> None:
    """Register PostgreSQL/legacy CI-only operator modes on the shared parser."""
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


def run_ci_once(reconcile: CiPass) -> None:
    """Report one trusted reconciliation after its transaction is complete."""
    observations = reconcile(True)
    print(
        "CI reconciliation: " + str(len(observations)) + " tracked observations",
        flush=True,
    )


def run_ci_watch(
    reconcile: CiPass,
    *,
    sleep: Callable[[float], None] = sleep,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Bounded CLI backoff; no false retry on a lost or denied leader."""
    delay = 2
    startup = True
    while stop is None or not stop():
        observations = reconcile(startup)
        startup = False
        if any(e.verdict == "BLOCKED" for e in observations):
            delay = min(60, delay * 2)
        else:
            delay = 2
        if stop is not None and stop():
            break
        sleep(delay)
